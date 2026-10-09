"""Validate semantic-query parameters against unit and model policy."""


def _column_for_name(name: str, model: dict):
    normalized = str(name).lower()
    for kind in ("dimensions", "measures"):
        for column in model.get(kind, []):
            real_name = column.get("column")
            aliases = column.get("aliases") or []
            if normalized == str(real_name).lower() or normalized in {
                str(alias).lower() for alias in aliases
            }:
                return column
    return None


def _normalize_filters(value, slot: str) -> list:
    if value is None:
        return []
    if isinstance(value, dict):
        if "column" in value:
            return [value]
        normalized = []
        for column, filter_value in value.items():
            if filter_value is None or filter_value == []:
                continue
            if isinstance(filter_value, dict):
                if "column" in filter_value:
                    normalized.append(filter_value)
                else:
                    normalized.append({"column": column, **filter_value})
                continue
            if isinstance(filter_value, list) and any(
                isinstance(item, dict) for item in filter_value
            ):
                for item in filter_value:
                    if not isinstance(item, dict):
                        raise ValueError(
                            f"'{slot}.{column}' mixes filter objects with scalar values"
                        )
                    if "column" in item:
                        normalized.append(item)
                    else:
                        normalized.append({"column": column, **item})
                continue
            normalized.append({
                "column": column,
                "operator": "IN" if isinstance(filter_value, list) else "=",
                "value": filter_value,
            })
        return normalized
    if not isinstance(value, list):
        raise ValueError(f"'{slot}' must be a list of filter objects")
    return value


def enforce(semantic_query: dict, unit: dict, model: dict) -> dict:
    """Canonicalize aliases and reject query fields outside this unit's contract."""
    query = dict(semantic_query)
    parameters = unit.get("parameters") or {}
    operation = unit.get("operation")
    controls = {"metric", "include_current_period"}
    if operation == "compare":
        controls.add("report_period_bounds")
    if operation == "ranked_breakdown":
        controls.add("rank_direction")
    unsupported = {
        key for key in set(query) - set(parameters) - controls
        if query[key] not in (None, [], {})
    }
    for key in set(query) - set(parameters) - controls:
        if key not in unsupported:
            query.pop(key)
    if unsupported:
        raise ValueError(
            f"Unsupported semantic-query fields for '{unit.get('name')}': "
            f"{sorted(unsupported)}"
        )

    filter_slots = {
        "filters", "scope_filters", "subgroup_filters",
        "period_a_filters", "period_b_filters", "conditions",
    }
    for slot, slot_value in list(query.items()):
        if slot == "group_by":
            if slot_value is None:
                slot_value = []
            if not isinstance(slot_value, list):
                raise ValueError("group_by must be a list")
            canonical_columns = []
            for name in slot_value:
                column = _column_for_name(name, model)
                if not column or column not in model.get("dimensions", []):
                    raise ValueError(f"group_by column '{name}' is not a model dimension")
                canonical_columns.append(column["column"])
            query[slot] = canonical_columns
            continue

        if slot not in filter_slots:
            continue
        filter_items = _normalize_filters(slot_value, slot)
        allowed_names = set((parameters.get(slot) or {}).get("optional", []))
        allowed_names.update((parameters.get(slot) or {}).get("required", []))
        allowed_columns = {
            column["column"].lower()
            for name in allowed_names
            if (column := _column_for_name(name, model))
        }
        canonical_items = []
        for item in filter_items:
            if not isinstance(item, dict) or not isinstance(item.get("column"), str):
                raise ValueError(f"Each item in '{slot}' must contain a column")
            column = _column_for_name(item["column"], model)
            if not column:
                raise ValueError(f"Unknown model column in '{slot}': {item['column']}")
            real_name = column["column"]
            if real_name.lower() not in allowed_columns:
                raise ValueError(
                    f"Column '{item['column']}' is not allowed in unit slot '{slot}'"
                )
            operator = str(item.get("operator", "=")).upper()
            filter_value = item["value"]
            if operator == "IN":
                if not isinstance(filter_value, list) or not filter_value:
                    raise ValueError(
                        f"'{slot}.{real_name}' uses IN but has no allowed values"
                    )
                if any(isinstance(value, (dict, list)) for value in filter_value):
                    raise ValueError(
                        f"'{slot}.{real_name}' IN values must be scalars"
                    )
            elif isinstance(filter_value, (dict, list)):
                raise ValueError(
                    f"'{slot}.{real_name}' operator {operator} requires one scalar value"
                )
            if slot == "conditions" and column in model.get("measures", []):
                if not column.get("conditional_filter"):
                    raise ValueError(
                        f"Measure '{real_name}' is not approved for conditional filters"
                    )
            normalized = dict(item)
            normalized["column"] = real_name
            normalized["operator"] = operator
            canonical_items.append(normalized)
        query[slot] = canonical_items

    group_spec = parameters.get("group_by") or {}
    required_group_by = group_spec.get("required") or []
    group_by = list(query.get("group_by") or [])
    present = {str(column).lower() for column in group_by}
    for name in required_group_by:
        column = _column_for_name(name, model)
        if not column or column not in model.get("dimensions", []):
            raise ValueError(f"Required group_by '{name}' is not a model dimension")
        if column["column"].lower() not in present:
            group_by.append(column["column"])
            present.add(column["column"].lower())
    query["group_by"] = group_by
    return query