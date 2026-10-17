"""Polyglot type generator: TypeScript, Pydantic v2 and Go from JSON.

Architecture
------------
Two front-ends produce one small intermediate representation (IR); three
back-ends render it.

* :func:`infer_from_sample` reads an example document. Every object found at
  the same position, for example all items of an array, is merged into one
  type. A field missing from some objects becomes optional; a field that is
  sometimes ``null`` becomes nullable; ``int`` merged with ``float`` widens to a
  number; anything else that mixes becomes a union.
* :func:`infer_from_schema` reads a JSON Schema (draft-04 to 2020-12 subset:
  ``type``, ``properties``, ``required``, ``items``, ``enum``, ``const``,
  ``anyOf``/``oneOf``, ``allOf``, ``additionalProperties``, ``nullable``,
  ``$ref`` into ``$defs``/``definitions``, ``title`` and ``description``).
* :func:`render_typescript`, :func:`render_pydantic` and :func:`render_go`
  print the IR.

:func:`generate_types` ties it together. In ``mode="auto"`` the input is
treated as a JSON Schema when it declares ``$schema`` or looks like one,
otherwise as a sample document.
"""

from __future__ import annotations

import json
import keyword
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Union

from .core import ensure_recursion_headroom

# --------------------------------------------------------------------- IR


@dataclass(frozen=True)
class Prim:
    kind: Literal["string", "integer", "number", "boolean", "null", "any"]


@dataclass(frozen=True)
class ArrayOf:
    item: TypeExpr


@dataclass(frozen=True)
class MapOf:
    value: TypeExpr


@dataclass(frozen=True)
class Named:
    name: str


@dataclass(frozen=True)
class Enum:
    values: tuple[Any, ...]


@dataclass(frozen=True)
class UnionOf:
    members: tuple[TypeExpr, ...]


TypeExpr = Union[Prim, ArrayOf, MapOf, Named, Enum, UnionOf]

NULL = Prim("null")
ANY = Prim("any")


@dataclass
class FieldDef:
    key: str
    type: TypeExpr
    optional: bool = False
    description: str | None = None


@dataclass
class ObjectDef:
    name: str
    fields: list[FieldDef] = field(default_factory=list)
    description: str | None = None


@dataclass
class TypeModel:
    root_name: str
    root: TypeExpr
    objects: dict[str, ObjectDef]
    source: Literal["sample", "schema"]

    def ordered(self, children_first: bool = False) -> list[ObjectDef]:
        """Objects reachable from the root, depth-first (pre- or post-order)."""
        seen: set[str] = set()
        out: list[ObjectDef] = []

        def visit_expr(expr: TypeExpr) -> None:
            for name in _named_refs(expr):
                visit(name)

        def visit(name: str) -> None:
            if name in seen or name not in self.objects:
                return
            seen.add(name)
            obj = self.objects[name]
            if not children_first:
                out.append(obj)
            for fdef in obj.fields:
                visit_expr(fdef.type)
            if children_first:
                out.append(obj)

        visit_expr(self.root)
        return out

    def cyclic(self) -> set[str]:
        """Names of objects that take part in a reference cycle."""
        graph = {name: {ref for f in obj.fields for ref in _named_refs(f.type)} for name, obj in self.objects.items()}
        cyclic: set[str] = set()
        for start in graph:
            stack, seen = list(graph[start]), set()
            while stack:
                node = stack.pop()
                if node == start:
                    cyclic.add(start)
                    break
                if node in seen or node not in graph:
                    continue
                seen.add(node)
                stack.extend(graph[node])
        return cyclic


def _named_refs(expr: TypeExpr) -> list[str]:
    if isinstance(expr, Named):
        return [expr.name]
    if isinstance(expr, ArrayOf):
        return _named_refs(expr.item)
    if isinstance(expr, MapOf):
        return _named_refs(expr.value)
    if isinstance(expr, UnionOf):
        return [name for member in expr.members for name in _named_refs(member)]
    return []


def union(*types: TypeExpr) -> TypeExpr:
    """Flatten, de-duplicate and simplify a union."""
    flat: list[TypeExpr] = []
    for t in types:
        for member in t.members if isinstance(t, UnionOf) else (t,):
            if member not in flat:
                flat.append(member)
    if ANY in flat:
        return ANY
    if Prim("integer") in flat and Prim("number") in flat:
        flat.remove(Prim("integer"))
    # Null goes last so renderers print "T | null".
    if NULL in flat:
        flat.remove(NULL)
        flat.append(NULL)
    if not flat:
        return ANY
    return flat[0] if len(flat) == 1 else UnionOf(tuple(flat))


def split_null(expr: TypeExpr) -> tuple[TypeExpr | None, bool]:
    """Return ``(type without null, nullable)``. ``None`` means "only null"."""
    if expr == NULL:
        return None, True
    if isinstance(expr, UnionOf) and NULL in expr.members:
        rest = tuple(m for m in expr.members if m != NULL)
        return (rest[0] if len(rest) == 1 else UnionOf(rest)), True
    return expr, False


# ------------------------------------------------------------------ naming

_RESERVED_TYPE_NAMES = {
    "Any", "Array", "BaseModel", "Boolean", "ConfigDict", "Date", "Dict", "Error", "Field", "Function",
    "List", "Literal", "Map", "Number", "Object", "Optional", "Promise", "Record", "RootModel", "Set",
    "String", "Symbol", "Union",
}


def _words(text: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def pascal_case(text: str, fallback: str = "Item") -> str:
    words = _words(text)
    name = "".join(w[:1].upper() + w[1:] for w in words) or fallback
    if name[0].isdigit():
        name = fallback + name
    return name


def singular(name: str) -> str:
    lower = name.lower()
    if lower.endswith("ies") and len(name) > 4:
        out = name[:-3] + "y"
    elif lower.endswith(("sses", "xes", "ches", "shes", "zes")):
        out = name[:-2]
    elif lower.endswith("s") and not lower.endswith(("ss", "us", "is")) and len(name) > 1:
        out = name[:-1]
    else:
        out = name
    return out if out != name else name + "Item"


class _Names:
    def __init__(self) -> None:
        self.taken: set[str] = set()

    def claim(self, hint: str) -> str:
        base = pascal_case(hint)
        if base in _RESERVED_TYPE_NAMES:
            base += "Model"
        name, n = base, 2
        while name in self.taken:
            name, n = f"{base}{n}", n + 1
        self.taken.add(name)
        return name


# --------------------------------------------------------- sample inference


class _Shape:
    """Accumulates every value seen at one position of the document."""

    __slots__ = ("kinds", "fields", "presence", "objects", "items", "arrays")

    def __init__(self) -> None:
        self.kinds: set[str] = set()
        self.fields: dict[str, _Shape] = {}
        self.presence: dict[str, int] = {}
        self.objects = 0
        self.items: _Shape | None = None
        self.arrays = 0

    def add(self, value: Any) -> None:
        if value is None:
            self.kinds.add("null")
        elif isinstance(value, bool):
            self.kinds.add("boolean")
        elif isinstance(value, int):
            self.kinds.add("integer")
        elif isinstance(value, float):
            self.kinds.add("number")
        elif isinstance(value, str):
            self.kinds.add("string")
        elif isinstance(value, dict):
            self.objects += 1
            for key, child in value.items():
                self.presence[key] = self.presence.get(key, 0) + 1
                self.fields.setdefault(key, _Shape()).add(child)
        elif isinstance(value, list):
            self.arrays += 1
            if self.items is None:
                self.items = _Shape()
            for child in value:
                self.items.add(child)
        else:
            raise TypeError(f"Unsupported value type: {type(value).__name__}")

    def empty(self) -> bool:
        return not self.kinds and not self.objects and not self.arrays


def infer_from_sample(value: Any, root_name: str = "RootObject") -> TypeModel:
    """Infer types from an example document (see module docs)."""
    ensure_recursion_headroom()
    shape = _Shape()
    shape.add(value)
    names = _Names()
    objects: dict[str, ObjectDef] = {}
    signatures: dict[tuple[str, str], str] = {}

    def convert(s: _Shape, hint: str, forced_name: str | None = None) -> TypeExpr:
        members: list[TypeExpr] = []
        for kind in ("string", "integer", "number", "boolean"):
            if kind in s.kinds:
                members.append(Prim(kind))  # type: ignore[arg-type]
        if s.objects:
            if not s.fields:
                members.append(MapOf(ANY))
            else:
                fields = [
                    FieldDef(key, convert(child, key), optional=s.presence[key] < s.objects)
                    for key, child in s.fields.items()
                ]
                base = pascal_case(forced_name or hint)
                signature = (base, repr([(f.key, f.type, f.optional) for f in fields]))
                if forced_name is None and signature in signatures:
                    name = signatures[signature]  # structurally identical: reuse
                else:
                    name = forced_name or names.claim(base)
                    objects[name] = ObjectDef(name, fields)
                    signatures[signature] = name
                members.append(Named(name))
        if s.arrays:
            item_hint = singular(pascal_case(hint))
            assert s.items is not None
            members.append(ArrayOf(ANY if s.items.empty() else convert(s.items, item_hint)))
        if "null" in s.kinds:
            members.append(NULL)
        return union(*members) if members else ANY

    root_hint = root_name if not isinstance(value, list) else root_name + "Item"
    if isinstance(value, list) and shape.items is not None and not shape.items.empty():
        root: TypeExpr = ArrayOf(convert(shape.items, root_hint))
    else:
        names.taken.add(root_name)
        root = convert(shape, root_name, forced_name=root_name)
    return TypeModel(root_name, root, objects, "sample")


# --------------------------------------------------------- schema inference

_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "null", "object", "array"}


def looks_like_schema(value: Any) -> bool:
    """Heuristic used by ``mode="auto"``."""
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("$schema"), str):
        return True
    declared = value.get("type")
    declared_ok = (isinstance(declared, str) and declared in _SCHEMA_TYPES) or (
        isinstance(declared, list) and declared and all(t in _SCHEMA_TYPES for t in declared)
    )
    if isinstance(value.get("properties"), dict) and (declared is None or declared == "object"):
        return all(isinstance(v, (dict, bool)) for v in value["properties"].values())
    return bool(declared_ok and any(k in value for k in ("items", "enum", "required", "$defs", "definitions")))


class SchemaError(ValueError):
    """The schema uses a construct this generator cannot resolve."""


def infer_from_schema(schema: Any, root_name: str = "RootObject") -> TypeModel:
    """Build the IR from a JSON Schema document (see module docs)."""
    ensure_recursion_headroom()
    if not isinstance(schema, dict):
        raise SchemaError("A JSON Schema must be an object")
    definitions: dict[str, Any] = {}
    for container in ("definitions", "$defs"):
        if isinstance(schema.get(container), dict):
            definitions.update(schema[container])
    names = _Names()
    names.taken.add(root_name)
    objects: dict[str, ObjectDef] = {}
    ref_names: dict[str, str] = {}
    resolving: set[str] = set()

    def resolve_ref(ref: str) -> TypeExpr:
        match = re.fullmatch(r"#/(?:\$defs|definitions)/(.+)", ref)
        if ref == "#":
            return Named(root_name)
        if not match:
            raise SchemaError(f"Only local $ref values are supported, got {ref!r}")
        key = match.group(1).replace("~1", "/").replace("~0", "~")
        if key not in definitions:
            raise SchemaError(f"$ref target not found: {ref}")
        if key in ref_names:
            return Named(ref_names[key])
        target = definitions[key]
        if _is_object_schema(target):
            name = names.claim(target.get("title") or key) if isinstance(target, dict) else names.claim(key)
            ref_names[key] = name
            convert(target, key, forced_name=name)
            return Named(name)
        if key in resolving:
            return ANY
        resolving.add(key)
        try:
            return convert(target, key)
        finally:
            resolving.discard(key)

    def convert(node: Any, hint: str, forced_name: str | None = None) -> TypeExpr:
        if node is True or node == {} or node is None:
            return ANY
        if node is False:
            return ANY
        if not isinstance(node, dict):
            raise SchemaError(f"Invalid schema node at {hint!r}")
        if "$ref" in node:
            return resolve_ref(node["$ref"])
        nullable = node.get("nullable") is True
        if "const" in node:
            return union(Enum((node["const"],)), *([NULL] if nullable else []))
        if isinstance(node.get("enum"), list):
            values = tuple(v for v in node["enum"] if v is not None)
            has_null = len(values) != len(node["enum"]) or nullable
            return union(*([Enum(values)] if values else []), *([NULL] if has_null else []))
        for combinator in ("anyOf", "oneOf"):
            if isinstance(node.get(combinator), list):
                options = [convert(option, f"{hint}Option{i + 1}") for i, option in enumerate(node[combinator])]
                return union(*options, *([NULL] if nullable else []))
        if isinstance(node.get("allOf"), list):
            merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
            for part in node["allOf"]:
                part = _deref(part, definitions)
                merged["properties"].update(part.get("properties", {}))
                merged["required"] += part.get("required", [])
                if "description" in part and "description" not in merged:
                    merged["description"] = part["description"]
            for key in ("title", "description"):
                if key in node:
                    merged[key] = node[key]
            return convert(merged, hint, forced_name)

        declared = node.get("type")
        kinds = declared if isinstance(declared, list) else [declared] if declared else []
        if not kinds:
            if "properties" in node or "additionalProperties" in node:
                kinds = ["object"]
            elif "items" in node:
                kinds = ["array"]
        members: list[TypeExpr] = []
        for kind in kinds:
            if kind == "object":
                members.append(convert_object(node, hint, forced_name))
            elif kind == "array":
                items = node.get("items")
                if isinstance(items, list):  # draft-04 tuple validation
                    item = union(*(convert(it, singular(pascal_case(hint))) for it in items)) if items else ANY
                elif isinstance(node.get("prefixItems"), list):
                    item = union(*(convert(it, singular(pascal_case(hint))) for it in node["prefixItems"]))
                else:
                    item = convert(items, singular(pascal_case(hint))) if items is not None else ANY
                members.append(ArrayOf(item))
            elif kind in ("string", "integer", "number", "boolean", "null"):
                members.append(Prim(kind))
        if nullable:
            members.append(NULL)
        return union(*members) if members else ANY

    def convert_object(node: dict[str, Any], hint: str, forced_name: str | None) -> TypeExpr:
        properties = node.get("properties")
        if not isinstance(properties, dict) or not properties:
            extra = node.get("additionalProperties")
            return MapOf(convert(extra, singular(pascal_case(hint))) if isinstance(extra, dict) else ANY)
        name = forced_name or names.claim(node.get("title") or hint)
        obj = ObjectDef(name, description=node.get("description"))
        objects[name] = obj
        required = set(node.get("required", []))
        for key, child in properties.items():
            child_type = convert(child, key)
            description = child.get("description") if isinstance(child, dict) else None
            obj.fields.append(FieldDef(key, child_type, optional=key not in required, description=description))
        return Named(name)

    root = convert(schema, root_name, forced_name=root_name if _is_object_schema(schema) else None)
    return TypeModel(root_name, root, objects, "schema")


def _is_object_schema(node: Any) -> bool:
    return (
        isinstance(node, dict)
        and isinstance(node.get("properties"), dict)
        and bool(node["properties"])
        and node.get("type", "object") in ("object", ["object"])
        and "$ref" not in node
    )


def _deref(node: Any, definitions: dict[str, Any]) -> dict[str, Any]:
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 32:
        match = re.fullmatch(r"#/(?:\$defs|definitions)/(.+)", node["$ref"])
        if not match or match.group(1) not in definitions:
            raise SchemaError(f"Cannot resolve $ref {node['$ref']!r} inside allOf")
        node = definitions[match.group(1)]
        seen += 1
    return node if isinstance(node, dict) else {}


# --------------------------------------------------------------- TypeScript

_TS_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _ts(expr: TypeExpr) -> str:
    if isinstance(expr, Prim):
        return {"string": "string", "integer": "number", "number": "number", "boolean": "boolean",
                "null": "null", "any": "unknown"}[expr.kind]
    if isinstance(expr, ArrayOf):
        inner = _ts(expr.item)
        return f"({inner})[]" if isinstance(expr.item, UnionOf) or (isinstance(expr.item, Enum) and len(expr.item.values) > 1) else f"{inner}[]"
    if isinstance(expr, MapOf):
        return f"Record<string, {_ts(expr.value)}>"
    if isinstance(expr, Named):
        return expr.name
    if isinstance(expr, Enum):
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in expr.values) or "never"
    return " | ".join(_ts(m) for m in expr.members)


def _ts_doc(text: str | None, indent: str) -> list[str]:
    """A TSDoc comment block (``*/`` inside the text is defused)."""
    if not text:
        return []
    lines = [line.replace("*/", "* /") for line in text.strip().splitlines()]
    if len(lines) == 1:
        return [f"{indent}/** {lines[0]} */"]
    return [f"{indent}/**", *(f"{indent} * {line}" for line in lines), f"{indent} */"]


def render_typescript(model: TypeModel) -> str:
    blocks: list[str] = []
    if not (isinstance(model.root, Named) and model.root.name == model.root_name):
        blocks.append(f"export type {model.root_name} = {_ts(model.root)};")
    for obj in model.ordered():
        lines = _ts_doc(obj.description, "")
        lines.append(f"export interface {obj.name} {{")
        for f in obj.fields:
            lines += _ts_doc(f.description, "  ")
            key = f.key if _TS_IDENTIFIER.match(f.key) else json.dumps(f.key, ensure_ascii=False)
            lines.append(f"  {key}{'?' if f.optional else ''}: {_ts(f.type)};")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


# ----------------------------------------------------------------- Pydantic

_PYDANTIC_RESERVED = {
    "copy", "dict", "json", "parse_obj", "parse_raw", "parse_file", "from_orm", "construct", "schema",
    "schema_json", "validate", "update_forward_refs", "fields",
}


def _py_literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is True:
        return "True"
    if value is False:
        return "False"
    return repr(value)


class _PyRenderer:
    def __init__(self, model: TypeModel):
        self.model = model
        self.imports: set[str] = set()
        self.pydantic: set[str] = set()

    def type(self, expr: TypeExpr) -> str:
        base, nullable = split_null(expr)
        if base is None:
            return "None"
        rendered = self._base(base)
        if nullable:
            self.imports.add("Optional")
            return f"Optional[{rendered}]"
        return rendered

    def _base(self, expr: TypeExpr) -> str:
        if isinstance(expr, Prim):
            if expr.kind == "any":
                self.imports.add("Any")
                return "Any"
            return {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "null": "None"}[expr.kind]
        if isinstance(expr, ArrayOf):
            self.imports.add("List")
            return f"List[{self.type(expr.item)}]"
        if isinstance(expr, MapOf):
            self.imports.add("Dict")
            return f"Dict[str, {self.type(expr.value)}]"
        if isinstance(expr, Named):
            return expr.name
        if isinstance(expr, Enum):
            self.imports.add("Literal")
            return f"Literal[{', '.join(_py_literal(v) for v in expr.values)}]"
        self.imports.add("Union")
        return f"Union[{', '.join(self.type(m) for m in expr.members)}]"

    def field_name(self, key: str, used: set[str], class_names: set[str]) -> str:
        valid = (
            key.isidentifier()
            and not keyword.iskeyword(key)
            and not key.startswith("_")
            and not key.startswith("model_")
            and key not in _PYDANTIC_RESERVED
            and key not in class_names
            and key not in {"Any", "Dict", "List", "Literal", "Optional", "Union", "Field", "BaseModel"}
        )
        name = key if valid else re.sub(r"\W+", "_", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)).strip("_").lower()
        if not name:
            name = "field"
        if name[0].isdigit():
            name = "field_" + name
        if not valid and (keyword.iskeyword(name) or name in _PYDANTIC_RESERVED or name.startswith("model_")
                          or name in class_names or not name.isidentifier()):
            name = name + "_"
        base, n = name, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        return name

    def render(self) -> str:
        model = self.model
        class_names = set(model.objects)
        cyclic = model.cyclic()
        body: list[str] = []
        for obj in model.ordered(children_first=True):
            self.pydantic.add("BaseModel")
            lines = [f"class {obj.name}(BaseModel):"]
            if obj.description:
                doc = obj.description.strip().replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
                lines.append(f'    """{doc}"""')
            field_lines: list[str] = []
            used: set[str] = set()
            aliased = False
            for f in obj.fields:
                name = self.field_name(f.key, used, class_names)
                annotation = self.type(f.type)
                _, nullable = split_null(f.type)
                args: list[str] = []
                if f.optional:
                    if not nullable and annotation != "None":
                        self.imports.add("Optional")
                        annotation = f"Optional[{annotation}]"
                    args.append("default=None")
                if name != f.key:
                    args.append(f"alias={json.dumps(f.key, ensure_ascii=False)}")
                    aliased = True
                if f.description:
                    args.append(f"description={json.dumps(f.description, ensure_ascii=False)}")
                if args == ["default=None"]:
                    field_lines.append(f"    {name}: {annotation} = None")
                elif args:
                    self.pydantic.add("Field")
                    field_lines.append(f"    {name}: {annotation} = Field({', '.join(args)})")
                else:
                    field_lines.append(f"    {name}: {annotation}")
            if aliased:
                self.pydantic.add("ConfigDict")
                lines.append("    model_config = ConfigDict(populate_by_name=True)")
                lines.append("")
            lines += field_lines  # object types always have at least one field
            body.append("\n".join(lines))
        if not (isinstance(model.root, Named) and model.root.name == model.root_name):
            self.pydantic.add("RootModel")
            body.append(f"class {model.root_name}(RootModel[{self.type(model.root)}]):\n    pass")
        rebuild = [f"{name}.model_rebuild()" for name in sorted(cyclic)]
        header: list[str] = []
        if cyclic:
            header.append("from __future__ import annotations\n")
        if self.imports:
            header.append(f"from typing import {', '.join(sorted(self.imports))}\n")
        header.append(f"from pydantic import {', '.join(sorted(self.pydantic))}")
        text = "\n".join(header) + "\n\n\n" + "\n\n\n".join(body) + "\n"
        if rebuild:
            text += "\n\n" + "\n".join(rebuild) + "\n"
        return text


def render_pydantic(model: TypeModel) -> str:
    return _PyRenderer(model).render()


# ----------------------------------------------------------------------- Go

_GO_INITIALISMS = {
    "acl", "api", "ascii", "cpu", "css", "dns", "eof", "guid", "html", "http", "https", "id", "ip", "json",
    "lhs", "qps", "ram", "rhs", "rpc", "sla", "smtp", "sql", "ssh", "tcp", "tls", "ttl", "udp", "ui", "uid",
    "uuid", "uri", "url", "utf8", "vm", "xml", "xmpp", "xsrf", "xss",
}


def go_name(key: str, fallback: str = "Field") -> str:
    words = _words(key)
    name = "".join(w.upper() if w.lower() in _GO_INITIALISMS else w[:1].upper() + w[1:] for w in words)
    if not name:
        return fallback
    if name[0].isdigit():
        name = fallback + name
    return name


def _go_scalar(expr: TypeExpr) -> str | None:
    if isinstance(expr, Prim):
        return {"string": "string", "integer": "int64", "number": "float64", "boolean": "bool"}.get(expr.kind)
    if isinstance(expr, Enum):
        kinds = {type(v) for v in expr.values}
        if kinds == {str}:
            return "string"
        if kinds == {bool}:
            return "bool"
        if kinds == {int}:
            return "int64"
        if kinds <= {int, float}:
            return "float64"
    return None


def _go(expr: TypeExpr, pointer: bool = False) -> str:
    base, nullable = split_null(expr)
    if base is None:
        return "any"
    scalar = _go_scalar(base)
    if scalar:
        return ("*" if nullable or pointer else "") + scalar
    if isinstance(base, Named):
        return ("*" if nullable or pointer else "") + base.name
    if isinstance(base, ArrayOf):
        return "[]" + _go(base.item)
    if isinstance(base, MapOf):
        return "map[string]" + _go(base.value)
    return "any"


def _go_tag(key: str, optional: bool) -> tuple[str, str | None]:
    if key == "" or "," in key:
        return '`json:"-"`', f"key {json.dumps(key)} cannot be expressed in an encoding/json tag; map it by hand"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    tag = f'json:"{escaped}{",omitempty" if optional else ""}"'
    if "`" in tag:
        return json.dumps(tag), None
    return f"`{tag}`", None


def render_go(model: TypeModel, package: str = "models") -> str:
    blocks = [f"package {package}"]
    if not (isinstance(model.root, Named) and model.root.name == model.root_name):
        blocks.append(f"type {model.root_name} {_go(model.root)}")
    for obj in model.ordered():
        rows: list[tuple[str, str, str] | str] = []
        used: set[str] = set()
        for f in obj.fields:
            name = go_name(f.key)
            base, n = name, 2
            while name in used:
                name, n = f"{base}{n}", n + 1
            used.add(name)
            go_type = _go(f.type, pointer=f.optional and not isinstance(split_null(f.type)[0], (ArrayOf, MapOf)))
            tag, note = _go_tag(f.key, f.optional)
            for line in ([f.description] if f.description else []) + ([note] if note else []):
                rows.extend(f"\t// {part}" for part in line.splitlines())
            rows.append((name, go_type, tag))
        lines = [f"// {line}" for line in (obj.description or "").splitlines()]
        lines.append(f"type {obj.name} struct {{")
        lines += _align_go(rows)
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _align_go(rows: list[tuple[str, str, str] | str]) -> list[str]:
    """Align struct fields the way gofmt does: runs of fields separated by
    comment lines are aligned together."""
    out: list[str] = []
    fields = [r for r in rows if isinstance(r, tuple)]
    name_width = max((len(r[0]) for r in fields), default=0)
    type_width = max((len(r[1]) for r in fields), default=0)
    for row in rows:
        if isinstance(row, str):
            out.append(row)
        else:
            name, go_type, tag = row
            out.append(f"\t{name.ljust(name_width)} {go_type.ljust(type_width)} {tag}")
    return out


# ------------------------------------------------------------------- public

Mode = Literal["auto", "sample", "schema"]


def build_model(value: Any, root_name: str = "RootObject", mode: Mode = "auto") -> TypeModel:
    root_name = pascal_case(root_name, "RootObject")
    if root_name in _RESERVED_TYPE_NAMES:
        root_name += "Model"
    if mode == "schema" or (mode == "auto" and looks_like_schema(value)):
        return infer_from_schema(value, root_name)
    return infer_from_sample(value, root_name)


def generate_types(value: Any, root_name: str = "RootObject", mode: Mode = "auto") -> dict[str, Any]:
    """Generate TypeScript, Pydantic and Go code for ``value``."""
    model = build_model(value, root_name, mode)
    return {
        "typescript": render_typescript(model),
        "pydantic": render_pydantic(model),
        "go": render_go(model),
        "mode": model.source,
        "root": model.root_name,
        "types": [obj.name for obj in model.ordered()],
    }
