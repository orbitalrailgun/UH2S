"""Приведение объектов CMDB JSM (Insight/Assets) к пригодному для таблицы виду.

Ответ Insight (`/rest/insight/1.0/iql/objects?includeAttributes=true`) отдаёт атрибуты объекта
списком, в котором есть только идентификатор атрибута (`objectTypeAttributeId`) и метаданные
значений — имени атрибута там нет. Состав и порядок атрибутов различаются даже у объектов одного
типа, поэтому обычное уплощение (`flatten_data`) даёт нестабильные колонки `attributes_0_...`,
`attributes_1_...`: запрос, написанный по одному объекту, ломается на другом. Мультизначные
ссылочные атрибуты дополнительно раздувают результат (одна ссылка на 400+ объектов — это ~36
метаданных на каждый, суммарно ~15 000 колонок в строке).

Здесь атрибуты сводятся к колонкам с человекочитаемыми именами (карта id -> имя приходит из
блока `objectTypeAttributes` ответа поиска или из `/objecttype/{id}/attributes`), метаданные
значений отбрасываются. Модуль чистый (без сети) — тестируется офлайн.
"""
from app.sources.additional.flatten import flatten_data

# служебное/раздувающее и уже разобранное — не переносим в «прочие поля»
_SKIP_FIELDS = ("attributes", "objectType", "objectTypeId", "avatar", "avatarUUID", "_links",
                "timestamp", "hasAvatar", "extendedInfo", "objectKey", "id", "label",
                "created", "updated", "archived")


def _object_type(obj):
    """(имя, id) типа объекта; objectType может быть dict или строкой (зависит от эндпоинта)."""
    object_type = obj.get("objectType")
    if isinstance(object_type, dict):
        return object_type.get("name"), object_type.get("id")
    return object_type, obj.get("objectTypeId")


def object_head(obj):
    """Полезные метаданные объекта для таблицы: ключ/id/метка/тип/даты/архив/ссылка.

    Аватары, иконки, `timestamp`, вложенный блок `objectType_*` и прочее служебное отбрасываются."""
    type_name, type_id = _object_type(obj)
    head = {"objectKey": obj.get("objectKey"), "id": obj.get("id"), "label": obj.get("label"),
            "objectType": type_name, "objectTypeId": type_id}
    for key in ("created", "updated", "archived"):
        if obj.get(key) is not None:
            head[key] = obj.get(key)
    links = obj.get("_links")
    if isinstance(links, dict) and links.get("self"):
        head["url"] = links.get("self")
    return head


def _attribute_id(attribute):
    """Идентификатор атрибута: `objectTypeAttributeId` или вложенный `objectTypeAttribute.id` (Assets)."""
    attr_id = attribute.get("objectTypeAttributeId")
    if attr_id is None:
        nested = attribute.get("objectTypeAttribute")
        if isinstance(nested, dict):
            attr_id = nested.get("id")
    return "" if attr_id is None else str(attr_id)


def _value_scalar(value):
    """Одно значение атрибута -> скаляр.

    Ссылка -> метка объекта, статус -> имя статуса, пользователь/группа -> имя; иначе `value`
    (машинный вид: ISO-дата, число), а при пустом `value` — `displayValue` (человекочитаемый)."""
    if not isinstance(value, dict):
        return value
    display = value.get("displayValue")
    referenced = value.get("referencedObject")
    if isinstance(referenced, dict):
        return referenced.get("label") or referenced.get("name") or referenced.get("objectKey") or display
    status = value.get("status")
    if isinstance(status, dict):
        return status.get("name") or display
    user = value.get("user")
    if isinstance(user, dict):
        return user.get("displayName") or user.get("name") or display
    group = value.get("group")
    if isinstance(group, dict):
        return group.get("name") or display
    raw = value.get("value")
    return raw if raw not in (None, "") else display


def _value_reference(value):
    """(objectKey, id) ссылочного значения или (None, None) — для длинной формы."""
    if isinstance(value, dict):
        referenced = value.get("referencedObject")
        if isinstance(referenced, dict):
            return referenced.get("objectKey"), referenced.get("id")
    return None, None


def attribute_scalars(attribute):
    """Значения атрибута списком скаляров (пустые значения пропускаются)."""
    values = attribute.get("objectAttributeValues")
    if not isinstance(values, list):
        return []
    scalars = []
    for item in values:
        scalar = _value_scalar(item)
        if scalar is None or scalar == "":
            continue
        scalars.append(scalar)
    return scalars


def _column_name(attribute, names, used):
    """Имя колонки для атрибута: имя из карты id->имя; иначе вложенное имя (Assets); иначе `attr_<id>`.

    Совпадение с уже занятым именем в этой же строке снимается суффиксом `[<id>]`."""
    attr_id = _attribute_id(attribute)
    name = (names or {}).get(attr_id)
    if not name:
        nested = attribute.get("objectTypeAttribute")
        if isinstance(nested, dict):
            name = nested.get("name") or nested.get("label")
    name = str(name).strip() if name else (f"attr_{attr_id}" if attr_id else "attr")
    if name in used:
        name = f"{name} [{attr_id}]" if attr_id else f"{name} [dup]"
    return name


def object_attributes(obj):
    """Список атрибутов объекта (пустой, если блок отсутствует — например, `includeAttributes=false`)."""
    attributes = obj.get("attributes")
    return attributes if isinstance(attributes, list) else []


def other_fields(obj):
    """Прочие поля объекта (кроме служебных и уже разобранных), уплощённые.

    Нужны только для ответов без блока `attributes` (у некоторых эндпоинтов/версий Insight форма
    другая) — иначе полезные данные потерялись бы. Пустой dict, если таких полей нет."""
    rest = {key: value for key, value in obj.items() if key not in _SKIP_FIELDS}
    return flatten_data(rest) if rest else {}


def attribute_name_map(payload):
    """Карта `{str(id): имя}` из блока `objectTypeAttributes` ответа поиска (`includeTypeAttributes=true`)."""
    names = {}
    if not isinstance(payload, dict):
        return names
    for item in payload.get("objectTypeAttributes") or []:
        if isinstance(item, dict) and item.get("id") is not None:
            label = item.get("name") or item.get("label")
            if label:
                names[str(item["id"])] = str(label)
    return names


def attribute_names_from_objects(objects):
    """Карта `{str(id): имя}` из самих объектов — Assets кладёт в атрибут вложенный `objectTypeAttribute`."""
    names = {}
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for attribute in object_attributes(obj):
            if not isinstance(attribute, dict):
                continue
            nested = attribute.get("objectTypeAttribute")
            if isinstance(nested, dict):
                label = nested.get("name") or nested.get("label")
                attr_id = _attribute_id(attribute)
                if label and attr_id:
                    names.setdefault(attr_id, str(label))
    return names


def object_type_ids_with_unnamed_attributes(objects, names):
    """id типов объектов, у которых остались атрибуты без имени, — для догрузки `/objecttype/{id}/attributes`."""
    type_ids = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        _, type_id = _object_type(obj)
        if type_id in (None, "") or type_id in type_ids:
            continue
        for attribute in object_attributes(obj):
            if isinstance(attribute, dict) and not (names or {}).get(_attribute_id(attribute)):
                type_ids.append(type_id)
                break
    return type_ids


def cmdb_objects_to_table(objects, names=None, sep="; ", max_values=0):
    """Объекты CMDB -> list of dict: строка на объект, колонка на атрибут (по имени атрибута).

    Мультизначные атрибуты склеиваются через `sep` (одно значение сохраняет исходный тип);
    `max_values > 0` ограничивает число значений в ячейке, остаток обозначается `… +N`."""
    rows = []
    for obj in objects:
        if not isinstance(obj, dict):
            rows.append({"value": obj})
            continue
        row = object_head(obj)
        attributes = object_attributes(obj)
        if not attributes:
            # форма ответа без блока attributes — переносим прочие поля, чтобы ничего не потерять
            for key, value in other_fields(obj).items():
                row.setdefault(key, value)
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            scalars = attribute_scalars(attribute)
            if not scalars:
                continue
            column = _column_name(attribute, names, row)
            if len(scalars) == 1:
                row[column] = scalars[0]
            elif max_values and len(scalars) > max_values:
                kept = sep.join(str(v) for v in scalars[:max_values])
                row[column] = f"{kept}{sep}… +{len(scalars) - max_values}"
            else:
                row[column] = sep.join(str(v) for v in scalars)
        rows.append(row)
    return rows


def cmdb_objects_to_long(objects, names=None):
    """Объекты CMDB -> длинная («узкая») форма: строка на каждое значение каждого атрибута.

    Колонки: метаданные объекта + `attribute`, `attribute_id`, `value_index`, `value`
    (+ `ref_objectKey`/`ref_id` для ссылочных значений). Удобно, когда схемы объектов разные."""
    rows = []
    for obj in objects:
        if not isinstance(obj, dict):
            rows.append({"value": obj})
            continue
        head = object_head(obj)
        attributes = object_attributes(obj)
        if not attributes:
            row = dict(head)
            row.update(other_fields(obj))
            rows.append(row)
            continue
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            attr_id = _attribute_id(attribute)
            column = _column_name(attribute, names, {})
            values = attribute.get("objectAttributeValues")
            values = values if isinstance(values, list) else []
            index = 0
            for item in values:
                scalar = _value_scalar(item)
                if scalar is None or scalar == "":
                    continue
                row = dict(head)
                row.update({"attribute": column, "attribute_id": attr_id,
                            "value_index": index, "value": scalar})
                ref_key, ref_id = _value_reference(item)
                if ref_key is not None or ref_id is not None:
                    row["ref_objectKey"] = ref_key
                    row["ref_id"] = ref_id
                rows.append(row)
                index += 1
    return rows
