"""Paths, descendants and subtotals for the chart of accounts."""
def account_path(group, groups):
    parts = []
    seen = set()
    while group and group.id not in seen:
        seen.add(group.id)
        parts.append(group.name)
        group = groups.get(group.parent_id)
    return ' → '.join(reversed(parts))


def descendant_ids(group_id, groups):
    result = {group_id}
    pending = [group_id]
    while pending:
        parent_id = pending.pop()
        for group in groups.values():
            if group.parent_id == parent_id and group.id not in result:
                result.add(group.id)
                pending.append(group.id)
    return result
