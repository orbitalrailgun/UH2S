"""Построение дерева (для SHOW ... tree) из табличной data (list of dict).

Модель рёбер: каждая строка — узел. Его идентичность — значение столбца `receive`; родитель узла — тот,
чей `receive` равен значению `transmit` этой строки (ребро transmit->receive = родитель->потомок).
Узел без родителя в наборе (или с пустым/само-ссылочным transmit) становится корнем -> получается лес.

Возврат build_tree: (roots, meta), где roots — список узлов {id, label, description, children},
пригодных для nicegui ui.tree (node_key='id', children_key='children'); meta — счётчики диагностики.
Без nicegui/БД: логика тестируется офлайн."""


def _s(value):
    """Значение ячейки -> строка (None -> ''). Числа/прочее приводятся к str."""
    if value is None:
        return ""
    return str(value)


def build_tree(rows, transmit, receive, title=None, description_fields=None, separator=" | "):
    """rows: list[dict]. transmit: столбец родительского id. receive: столбец собственного id узла.
    title: столбец имени узла (по умолчанию — сам id). description_fields: список столбцов, значения
    которых конкатенируются через separator в описание (показывается при раскрытии узла).
    Возврат (roots, meta)."""
    description_fields = description_fields or []

    nodes_by_id = {}    # id -> узел
    order = []          # порядок появления (для стабильного порядка корней/детей)
    parent_of = {}      # id -> raw-значение родителя (transmit)
    duplicates = 0
    skipped_no_id = 0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        node_id = _s(row.get(receive)).strip()
        if node_id == "":
            skipped_no_id += 1
            continue
        if node_id in nodes_by_id:
            duplicates += 1
            continue
        label = _s(row.get(title)).strip() if title else ""
        if label == "":
            label = node_id
        description = separator.join(
            _s(row.get(field)) for field in description_fields if _s(row.get(field)).strip() != "")
        nodes_by_id[node_id] = {"id": node_id, "label": label, "description": description, "children": []}
        order.append(node_id)
        parent_of[node_id] = _s(row.get(transmit)).strip()

    def _creates_cycle(child_id, parent_id):
        """Создаст ли привязка child под parent цикл: есть ли child_id в цепочке предков parent."""
        seen = set()
        current = parent_id
        while current and current in nodes_by_id and current not in seen:
            if current == child_id:
                return True
            seen.add(current)
            current = parent_of.get(current, "")
        return False

    roots = []
    cycles_broken = 0
    for node_id in order:
        parent_id = parent_of.get(node_id, "")
        if parent_id == "" or parent_id == node_id or parent_id not in nodes_by_id:
            roots.append(nodes_by_id[node_id])          # нет родителя в наборе / само-ссылка -> корень
        elif _creates_cycle(node_id, parent_id):
            cycles_broken += 1
            roots.append(nodes_by_id[node_id])          # разрыв цикла: узел становится корнем
        else:
            nodes_by_id[parent_id]["children"].append(nodes_by_id[node_id])

    meta = {"nodes": len(nodes_by_id), "roots": len(roots),
            "duplicates": duplicates, "skipped_no_id": skipped_no_id, "cycles_broken": cycles_broken}
    return roots, meta


def tree_to_text(roots, indent="  "):
    """ASCII-представление дерева (для текстового/API-вывода). Описание — в скобках после имени."""
    lines = []

    def walk(node, depth):
        prefix = indent * depth
        label = node.get("label", node.get("id", ""))
        description = node.get("description") or ""
        suffix = f"  ({description})" if description else ""
        lines.append(f"{prefix}- {label}{suffix}")
        for child in node.get("children", []) or []:
            walk(child, depth + 1)

    for root in roots or []:
        walk(root, 0)
    return "\n".join(lines)
