from tree_sitter import Node

from ... import constants as cs
from ..utils import contains_node, safe_decode_text


def extract_assigned_name(
    target_node: Node, accepted_var_types: tuple[str, ...] = cs.LUA_DEFAULT_VAR_TYPES
) -> str | None:
    current = target_node.parent
    while current and current.type != cs.TS_LUA_ASSIGNMENT_STATEMENT:
        current = current.parent

    if not current:
        return None

    expression_list = next(
        (
            child
            for child in current.children
            if child.type == cs.TS_LUA_EXPRESSION_LIST
        ),
        None,
    )
    if not expression_list:
        return None

    values = []
    values.extend(
        expression_list.child(i)
        for i in range(expression_list.child_count)
        if expression_list.field_name_for_child(i) == cs.FIELD_VALUE
    )
    target_index = next(
        (
            idx
            for idx, value in enumerate(values)
            if value == target_node or contains_node(value, target_node)
        ),
        -1,
    )
    if target_index == -1:
        return None

    variable_list = next(
        (child for child in current.children if child.type == cs.TS_LUA_VARIABLE_LIST),
        None,
    )
    if not variable_list:
        return None

    names = []
    names.extend(
        variable_list.child(i)
        for i in range(variable_list.child_count)
        if variable_list.field_name_for_child(i) == cs.FIELD_NAME
    )
    if target_index < len(names):
        var_child = names[target_index]
        if var_child.type in accepted_var_types:
            return safe_decode_text(var_child)

    return None


def find_ancestor_statement(node: Node) -> Node | None:
    stmt = node.parent
    while stmt and not (
        stmt.type.endswith(cs.LUA_STATEMENT_SUFFIX)
        or stmt.type in {cs.TS_LUA_ASSIGNMENT_STATEMENT, cs.TS_LUA_LOCAL_STATEMENT}
    ):
        stmt = stmt.parent
    return stmt


def extract_pcall_second_identifier(call_node: Node) -> str | None:
    stmt = find_ancestor_statement(call_node)
    if not stmt:
        return None

    variable_list = next(
        (child for child in stmt.children if child.type == cs.TS_LUA_VARIABLE_LIST),
        None,
    )
    if not variable_list:
        return None

    names = []
    for i in range(variable_list.child_count):
        if variable_list.field_name_for_child(i) == cs.FIELD_NAME:
            name_node = variable_list.child(i)
            if name_node and name_node.type == cs.TS_LUA_IDENTIFIER:
                if decoded := safe_decode_text(name_node):
                    names.append(decoded)

    return names[1] if len(names) >= 2 else None


def field_key_name(field: Node) -> str | None:
    """The key a table-constructor `field` binds: `f` in `f = ...`, `set` in
    `["set"] = ...`. None for a positional entry or a computed key.

    tree-sitter-lua exposes `k = v` and `[k] = v` alike as `name: identifier`;
    the opening bracket is what tells a computed key from a literal one
    (#1631 review), so a bracketed identifier is computed and names nothing.
    """
    key = field.child_by_field_name(cs.FIELD_NAME)
    if key is None:
        return None
    bracketed = bool(field.children) and field.children[0].type == cs.LUA_OPEN_BRACKET
    if key.type == cs.TS_LUA_IDENTIFIER:
        return None if bracketed else safe_decode_text(key)
    if key.type in cs.LUA_STRING_TYPES:
        content = next(
            (c for c in key.named_children if c.type == cs.TS_LUA_STRING_CONTENT),
            None,
        )
        return safe_decode_text(content) if content is not None else None
    return None


def field_function_path(func_node: Node) -> tuple[str, str] | None:
    """(`table.key` path, `key`) for a function that is a table field's value.

    Shared by the definition pass, which registers the node under the path,
    and the call pass, which must recover the same name or the body's calls
    are skipped or credited to the enclosing function (#1631 review). Nested
    constructors chain their keys (`M = { sub = { f = ... } }` gives
    `M.sub.f`); the outermost table takes the name its statement assigns it,
    through `extract_assigned_name`. A constructor with no assignment (a
    returned or passed table) names the function by its keys alone.

    None when the function is not a field value, when its key is positional
    or computed, or when any enclosing constructor sits in a positional or
    computed field: `{ { run = function() end } }` has no field `run` on the
    outer list, and inventing `list.run` brings back the `@line` collisions
    this exists to remove. The caller then falls back to the assignment form.
    """
    field = func_node.parent
    if (
        field is None
        or field.type != cs.TS_LUA_FIELD
        or field.child_by_field_name(cs.FIELD_VALUE) != func_node
    ):
        return None
    key = field_key_name(field)
    if not key:
        return None
    parts = [key]
    table = field.parent
    while table is not None and table.type == cs.TS_LUA_TABLE_CONSTRUCTOR:
        enclosing = table.parent
        if enclosing is None or enclosing.type != cs.TS_LUA_FIELD:
            break
        outer_key = field_key_name(enclosing)
        if not outer_key:
            return None
        parts.insert(0, outer_key)
        table = enclosing.parent
    if table is not None:
        owner = extract_assigned_name(
            table, accepted_var_types=(cs.TS_DOT_INDEX_EXPRESSION, cs.TS_IDENTIFIER)
        )
        if owner:
            parts.insert(0, owner)
    return cs.SEPARATOR_DOT.join(parts), key
