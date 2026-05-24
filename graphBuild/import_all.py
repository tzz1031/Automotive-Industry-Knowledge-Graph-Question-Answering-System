from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CAR_DATA = BASE_DIR / "data" / "car_data" / "car_data.json"
DEFAULT_ENTITY_DATA = BASE_DIR / "data" / "car_data" / "car_entity.json"

DEFAULT_URI = os.getenv("NEO4J_URI", "http://localhost:7474")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", "12qwaszx")
DEFAULT_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

BATCH_SIZE = 300

SUPPLY_RELATIONS = {
    "battery_cell": "USES_BATTERY_CELL",
    "battery_pack": "USES_BATTERY_PACK",
    "motor": "USES_MOTOR_SUPPLIER",
    "adas_chip": "USES_ADAS_CHIP",
    "cockpit_soc": "USES_COCKPIT_SOC",
    "lidar": "USES_LIDAR",
    "smart_drive_software": "USES_SMART_DRIVE",
    "chassis": "USES_CHASSIS",
    "paint": "USES_PAINT",
    "glass": "USES_GLASS",
    "seat": "USES_SEAT",
    "tire": "USES_TIRE",
}

LEGACY_SUPPLIER_FIELDS = {
    "battery_cell_supplier": "battery_cell",
    "battery_supplier": "battery_pack",
    "motor_supplier": "motor",
    "adas_chip_supplier": "adas_chip",
    "soc_chip_supplier": "cockpit_soc",
    "lidar_supplier": "lidar",
    "smart_drive_sw_supplier": "smart_drive_software",
    "chassis_supplier_supplier": "chassis",
    "paint_supplier": "paint",
    "glass_supplier": "glass",
    "seat_supplier": "seat",
    "tire_supplier": "tire",
}

TECH_RELATION_TYPES = {
    "研发企业": "DEVELOPED_BY",
    "发布": "PUBLISHED",
    "推出": "LAUNCHED",
    "采用": "USES_TECH",
    "搭载": "EQUIPPED_WITH",
    "支持": "SUPPORTS",
    "包含": "CONTAINS",
    "包含能力": "HAS_CAPABILITY",
    "用于": "USED_FOR",
    "适用场景": "APPLIES_TO",
    "依托": "BASED_ON",
    "通过测试": "PASSED_TEST",
    "规范对象": "REGULATES",
    "正极材料": "CATHODE_MATERIAL",
    "融合传感器": "FUSES_SENSOR",
    "合作": "COOPERATES_WITH",
    "进入": "ENTERS",
    "超越": "SURPASSES",
    "实施": "IMPLEMENTS",
    "蝉联": "RETAINS",
    "新能源汽车出口": "EXPORTS_NEV",
}


class Neo4jHttpClient:
    def __init__(self, uri: str, user: str, password: str, database: str = DEFAULT_DATABASE):
        self.uri = uri.rstrip("/")
        self.database = database.strip("/") or "neo4j"
        self.endpoint = f"{self.uri}/db/{self.database}/tx/commit"
        self.legacy_endpoint = f"{self.uri}/db/data/transaction/commit"
        self._legacy_mode = False
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
        }

    def run(self, statement: str, parameters: dict[str, Any] | None = None) -> list[list[Any]]:
        payload = {"statements": [{"statement": statement, "parameters": parameters or {}}]}
        data = self._post(payload)
        if data.get("errors"):
            raise Neo4jQueryError(data["errors"])
        rows = data.get("results", [{}])[0].get("data", [])
        return [row.get("row", []) for row in rows]

    def batch(self, statements: list[dict[str, Any]]) -> None:
        if not statements:
            return
        data = self._post({"statements": statements})
        if data.get("errors"):
            raise Neo4jQueryError(data["errors"])

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        endpoint = self.legacy_endpoint if self._legacy_mode else self.endpoint
        req = urllib.request.Request(endpoint, body, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and not self._legacy_mode:
                self._legacy_mode = True
                legacy_req = urllib.request.Request(self.legacy_endpoint, body, headers=self.headers)
                try:
                    with urllib.request.urlopen(legacy_req, timeout=30) as resp:
                        print(f"  Neo4j HTTP endpoint fallback: {self.legacy_endpoint}")
                        return json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as legacy_exc:
                    detail = legacy_exc.read().decode("utf-8", errors="ignore")
                    raise RuntimeError(
                        f"Neo4j HTTP endpoint not found.\n"
                        f"Tried: {self.endpoint}\n"
                        f"Tried: {self.legacy_endpoint}\n"
                        f"Last error: HTTP {legacy_exc.code} {legacy_exc.reason}\n{detail}"
                    ) from legacy_exc
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Neo4j HTTP request failed: HTTP {exc.code} {exc.reason}\n"
                f"Endpoint: {endpoint}\n{detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot connect to Neo4j at {self.uri}: {exc}") from exc


class Neo4jQueryError(RuntimeError):
    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__(json.dumps(errors, ensure_ascii=False, indent=2))

    def has_code(self, fragment: str) -> bool:
        return any(fragment in str(error.get("code", "")) for error in self.errors)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_input_path(value: str, default_path: Path) -> Path:
    """Resolve PyCharm-friendly paths.

    If a run configuration passes a relative path, try it from the current
    working directory first and then from the project root.
    """
    raw = str(value or "").strip()
    if not raw:
        return default_path

    path = Path(raw).expanduser()
    if path.is_absolute():
        return path

    cwd_path = (Path.cwd() / path).resolve()
    if cwd_path.exists():
        return cwd_path

    base_path = (BASE_DIR / path).resolve()
    if base_path.exists():
        return base_path

    return base_path


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"{label} not found: {path}\n"
            f"Project root detected as: {BASE_DIR}\n"
            "Check the PyCharm working directory or pass an absolute path."
        )
    if not path.is_file():
        raise FileNotFoundError(f"{label} is not a file: {path}")


def as_list(value: Any) -> list[Any]:
    if value in (None, "", [], {}, "None", "[]"):
        return []
    if isinstance(value, list):
        return [v for v in value if v not in (None, "", "None", "[]")]
    return [value]


def compact_props(props: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in props.items():
        if value in (None, "", [], {}, "None", "[]"):
            continue
        if isinstance(value, (dict, list)):
            clean[key] = json.dumps(value, ensure_ascii=False)
        else:
            clean[key] = value
    return clean


def attr_key(name: str) -> str:
    key = re.sub(r"[^\w\u4e00-\u9fff]", "_", str(name)).strip("_")
    if not key:
        key = "attr"
    if re.match(r"^\d", key):
        key = f"attr_{key}"
    return key[:60]


def rel_type(name: str) -> str:
    mapped = TECH_RELATION_TYPES.get(str(name).strip())
    if mapped:
        return mapped
    key = re.sub(r"[^A-Za-z0-9_]", "_", str(name).upper()).strip("_")
    return f"TECH_{key[:40]}" if key else "RELATED_TO"


def product_id(supplier: str, product: str) -> str:
    return f"{supplier}::{product}"


def iter_supplier_entities(car: dict[str, Any]) -> list[dict[str, str]]:
    entities = car.get("supplier_entities")
    if isinstance(entities, list) and entities:
        result = []
        for item in entities:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            supplier = str(item.get("supplier", "")).strip()
            product = str(item.get("product", "")).strip()
            raw = str(item.get("raw", "")).strip()
            if role and supplier:
                result.append({"role": role, "supplier": supplier, "product": product, "raw": raw})
        return result

    result = []
    for field, role in LEGACY_SUPPLIER_FIELDS.items():
        for raw in as_list(car.get(field)):
            text = str(raw).strip()
            if text:
                result.append({"role": role, "supplier": text, "product": "", "raw": text})
    return result


def create_constraints(db: Neo4jHttpClient) -> None:
    constraints = [
        ("Car", "node_id", "car_node_id"),
        ("Brand", "name", "brand_name"),
        ("Series", "node_id", "series_node_id"),
        ("Motor", "node_id", "motor_node_id"),
        ("Battery", "node_id", "battery_node_id"),
        ("Supplier", "name", "supplier_name"),
        ("SupplierProduct", "node_id", "supplier_product_id"),
        ("Technology", "node_id", "technology_node_id"),
    ]
    for label, prop, name in constraints:
        modern = f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
        legacy = f"CREATE CONSTRAINT ON (n:{label}) ASSERT n.{prop} IS UNIQUE"
        try:
            db.run(modern)
        except Neo4jQueryError as exc:
            if exc.has_code("SyntaxError"):
                try:
                    db.run(legacy)
                except Neo4jQueryError as legacy_exc:
                    if legacy_exc.has_code("ConstraintAlreadyExists"):
                        continue
                    raise
            elif exc.has_code("ConstraintAlreadyExists"):
                continue
            else:
                raise


def flush(db: Neo4jHttpClient, statements: list[dict[str, Any]]) -> None:
    db.batch(statements)
    statements.clear()


def import_cars(db: Neo4jHttpClient, cars: list[dict[str, Any]]) -> dict[str, int]:
    stats = {
        "Car": 0,
        "Brand": 0,
        "Series": 0,
        "Motor": 0,
        "Battery": 0,
        "Supplier": 0,
        "SupplierProduct": 0,
        "VehicleRelations": 0,
    }
    seen_brands: set[str] = set()
    seen_series: set[str] = set()
    seen_suppliers: set[str] = set()
    seen_products: set[str] = set()
    statements: list[dict[str, Any]] = []

    for car in cars:
        cid = str(car.get("car_id") or car.get("model_id") or "").strip()
        if not cid:
            continue

        brand = str(car.get("brand_name", "")).strip()
        series = str(car.get("series_name", "")).strip()

        car_props = compact_props(
            {
                "node_id": cid,
                "name": car.get("car_name", ""),
                "full_name": car.get("full_name", ""),
                "trim_name": car.get("trim_name", car.get("car_name", "")),
                "brand": brand,
                "series": series,
                "manufacturer": car.get("manufacturer", ""),
                "price_wan": car.get("price_wan"),
                "price_segment": car.get("price_segment", ""),
                "energy_category": car.get("energy_category", ""),
                "powertrain_type": car.get("powertrain_type", ""),
                "is_new_energy": car.get("is_new_energy"),
                "range_km": car.get("range_km"),
                "range_segment": car.get("range_segment", ""),
                "acceleration": car.get("acceleration_0_100_num"),
                "top_speed": car.get("top_speed_num"),
                "length_mm": car.get("length_mm_num"),
                "width_mm": car.get("width_mm_num"),
                "height_mm": car.get("height_mm_num"),
                "wheelbase_mm": car.get("wheelbase_mm_num"),
                "vehicle_class": car.get("car_class", ""),
                "body_type": car.get("body_structure", ""),
                "body_category": car.get("body_category", ""),
                "seats": car.get("seats_num"),
                "drive_type": car.get("drive_type", ""),
                "launch_date": car.get("launch_date", ""),
                "curb_weight_kg": car.get("curb_weight_kg_num"),
                "energy_consumption": car.get("energy_consumption_per_100km_num"),
                "fast_charge_power_kw": car.get("fast_charge_power_kw_num"),
                "has_lidar": car.get("has_lidar"),
                "lidar_count": car.get("lidar_count"),
                "search_aliases": car.get("search_aliases", []),
            }
        )
        statements.append(
            {
                "statement": "MERGE (c:Car {node_id: $node_id}) SET c += $props",
                "parameters": {"node_id": cid, "props": car_props},
            }
        )
        stats["Car"] += 1

        if brand:
            if brand not in seen_brands:
                statements.append(
                    {
                        "statement": "MERGE (:Brand {name: $name})",
                        "parameters": {"name": brand},
                    }
                )
                seen_brands.add(brand)
                stats["Brand"] += 1
            statements.append(
                {
                    "statement": """
                    MATCH (c:Car {node_id: $cid})
                    MATCH (b:Brand {name: $brand})
                    MERGE (c)-[:BELONGS_TO_BRAND]->(b)
                    """,
                    "parameters": {"cid": cid, "brand": brand},
                }
            )
            stats["VehicleRelations"] += 1

        if series:
            if series not in seen_series:
                statements.append(
                    {
                        "statement": """
                        MERGE (s:Series {node_id: $node_id})
                        SET s.name = $name, s.brand = $brand
                        """,
                        "parameters": {"node_id": series, "name": series, "brand": brand},
                    }
                )
                seen_series.add(series)
                stats["Series"] += 1
            statements.append(
                {
                    "statement": """
                    MATCH (c:Car {node_id: $cid})
                    MATCH (s:Series {node_id: $series})
                    MERGE (c)-[:BELONGS_TO_SERIES]->(s)
                    """,
                    "parameters": {"cid": cid, "series": series},
                }
            )
            stats["VehicleRelations"] += 1
            if brand:
                statements.append(
                    {
                        "statement": """
                        MATCH (s:Series {node_id: $series})
                        MATCH (b:Brand {name: $brand})
                        MERGE (s)-[:SERIES_OF_BRAND]->(b)
                        """,
                        "parameters": {"series": series, "brand": brand},
                    }
                )
                stats["VehicleRelations"] += 1

        motor_props = compact_props(
            {
                "node_id": f"{cid}_motor",
                "name": f"{car.get('car_name', '')} 电动机",
                "power_kw": car.get("motor_power_kw_num"),
                "torque_nm": car.get("motor_torque_nm_num"),
                "motor_type": car.get("motor_type", ""),
                "motor_count": car.get("motor_count", ""),
            }
        )
        if len(motor_props) > 2:
            statements.append(
                {
                    "statement": "MERGE (m:Motor {node_id: $node_id}) SET m += $props",
                    "parameters": {"node_id": motor_props["node_id"], "props": motor_props},
                }
            )
            statements.append(
                {
                    "statement": """
                    MATCH (c:Car {node_id: $cid})
                    MATCH (m:Motor {node_id: $mid})
                    MERGE (c)-[:HAS_MOTOR]->(m)
                    """,
                    "parameters": {"cid": cid, "mid": motor_props["node_id"]},
                }
            )
            stats["Motor"] += 1
            stats["VehicleRelations"] += 1

        battery_props = compact_props(
            {
                "node_id": f"{cid}_battery",
                "name": f"{car.get('car_name', '')} 电池",
                "capacity_kwh": car.get("battery_capacity_kwh_num"),
                "battery_type": car.get("battery_type", ""),
                "battery_brand": car.get("battery_brand", ""),
                "energy_density_whkg": car.get("battery_energy_density"),
            }
        )
        if len(battery_props) > 2:
            statements.append(
                {
                    "statement": "MERGE (b:Battery {node_id: $node_id}) SET b += $props",
                    "parameters": {"node_id": battery_props["node_id"], "props": battery_props},
                }
            )
            statements.append(
                {
                    "statement": """
                    MATCH (c:Car {node_id: $cid})
                    MATCH (b:Battery {node_id: $bid})
                    MERGE (c)-[:HAS_BATTERY]->(b)
                    """,
                    "parameters": {"cid": cid, "bid": battery_props["node_id"]},
                }
            )
            stats["Battery"] += 1
            stats["VehicleRelations"] += 1

        for item in iter_supplier_entities(car):
            role = item["role"]
            supplier = item["supplier"]
            product = item["product"]
            raw = item["raw"]
            relation = SUPPLY_RELATIONS.get(role, "USES_SUPPLIER")

            if supplier not in seen_suppliers:
                statements.append(
                    {
                        "statement": "MERGE (s:Supplier {name: $name})",
                        "parameters": {"name": supplier},
                    }
                )
                seen_suppliers.add(supplier)
                stats["Supplier"] += 1

            statements.append(
                {
                    "statement": f"""
                    MATCH (c:Car {{node_id: $cid}})
                    MATCH (s:Supplier {{name: $supplier}})
                    MERGE (c)-[r:{relation}]->(s)
                    SET r.role = $role, r.raw = $raw
                    """,
                    "parameters": {"cid": cid, "supplier": supplier, "role": role, "raw": raw},
                }
            )
            stats["VehicleRelations"] += 1

            if product:
                pid = product_id(supplier, product)
                if pid not in seen_products:
                    statements.append(
                        {
                            "statement": """
                            MERGE (p:SupplierProduct {node_id: $node_id})
                            SET p.name = $name, p.supplier = $supplier
                            """,
                            "parameters": {"node_id": pid, "name": product, "supplier": supplier},
                        }
                    )
                    statements.append(
                        {
                            "statement": """
                            MATCH (s:Supplier {name: $supplier})
                            MATCH (p:SupplierProduct {node_id: $pid})
                            MERGE (s)-[:PROVIDES_PRODUCT]->(p)
                            """,
                            "parameters": {"supplier": supplier, "pid": pid},
                        }
                    )
                    seen_products.add(pid)
                    stats["SupplierProduct"] += 1
                    stats["VehicleRelations"] += 1
                statements.append(
                    {
                        "statement": """
                        MATCH (c:Car {node_id: $cid})
                        MATCH (p:SupplierProduct {node_id: $pid})
                        MERGE (c)-[r:USES_PRODUCT]->(p)
                        SET r.role = $role
                        """,
                        "parameters": {"cid": cid, "pid": pid, "role": role},
                    }
                )
                stats["VehicleRelations"] += 1

        if len(statements) >= BATCH_SIZE:
            flush(db, statements)

    flush(db, statements)
    return stats


def import_entities(db: Neo4jHttpClient, entity_graph: dict[str, Any]) -> dict[str, int]:
    entities = entity_graph.get("entities", {})
    stats = {"Technology": 0, "TechAttributes": 0, "TechRelations": 0}
    statements: list[dict[str, Any]] = []

    for name, entity in entities.items():
        if not isinstance(entity, dict):
            continue
        attrs = entity.get("attributes", {}) if isinstance(entity.get("attributes", {}), dict) else {}
        props = {
            "node_id": name,
            "name": name,
            "category": entity.get("category", "Technology"),
            "entity_type": entity.get("type", "entity"),
            "attributes_json": json.dumps(attrs, ensure_ascii=False),
            "sources": entity.get("sources", []),
            "graph_sources": entity.get("graph_sources", []),
        }
        for key, value in attrs.items():
            props[attr_key(key)] = value if isinstance(value, (int, float, bool)) else str(value)[:500]
            stats["TechAttributes"] += 1

        props = compact_props(props)
        statements.append(
            {
                "statement": "MERGE (t:Technology {node_id: $node_id}) SET t += $props",
                "parameters": {"node_id": name, "props": props},
            }
        )
        stats["Technology"] += 1

        for rel in entity.get("relations", []):
            if not isinstance(rel, dict):
                continue
            rname = str(rel.get("relation", "")).strip()
            if not rname:
                continue
            for obj in as_list(rel.get("object")):
                target = str(obj).strip()
                if not target:
                    continue
                rtype = rel_type(rname)
                evidence = rel.get("evidence", [])
                sources = rel.get("sources", [])
                statements.append(
                    {
                        "statement": f"""
                        MATCH (a:Technology {{node_id: $source}})
                        MERGE (b:Technology {{node_id: $target}})
                        SET b.name = coalesce(b.name, $target)
                        MERGE (a)-[r:{rtype}]->(b)
                        SET r.name = $relation,
                            r.sources = $sources,
                            r.evidence = $evidence
                        """,
                        "parameters": {
                            "source": name,
                            "target": target,
                            "relation": rname,
                            "sources": sources if isinstance(sources, list) else [str(sources)],
                            "evidence": evidence if isinstance(evidence, list) else [str(evidence)],
                        },
                    }
                )
                stats["TechRelations"] += 1

        if len(statements) >= BATCH_SIZE:
            flush(db, statements)

    flush(db, statements)
    return stats


def print_db_counts(db: Neo4jHttpClient) -> None:
    rows = db.run(
        """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(*) AS count
        ORDER BY label
        """
    )
    print("\nNeo4j node counts:")
    for label, count in rows:
        print(f"  {label}: {count}")

    rel_rows = db.run("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC")
    print("\nNeo4j relationship counts:")
    for rtype, count in rel_rows[:30]:
        print(f"  {rtype}: {count}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import car_data.json and car_entity.json into Neo4j.")
    parser.add_argument("--car-data", default=str(DEFAULT_CAR_DATA), help="Path to data/car_data/car_data.json.")
    parser.add_argument("--entity-data", default=str(DEFAULT_ENTITY_DATA), help="Path to data/car_data/car_entity.json.")
    parser.add_argument("--uri", default=DEFAULT_URI, help="Neo4j HTTP URI, default from NEO4J_URI or http://localhost:7474.")
    parser.add_argument("--user", default=DEFAULT_USER, help="Neo4j user, default from NEO4J_USER or neo4j.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Neo4j password, default from NEO4J_PASSWORD or project default.")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="Neo4j database name, default from NEO4J_DATABASE or neo4j.")
    parser.add_argument("--no-clear", action="store_true", help="Do not delete existing Neo4j graph before importing.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    car_path = resolve_input_path(args.car_data, DEFAULT_CAR_DATA)
    entity_path = resolve_input_path(args.entity_data, DEFAULT_ENTITY_DATA)

    require_file(car_path, "car_data.json")
    require_file(entity_path, "car_entity.json")

    cars = load_json(car_path)
    entity_graph = load_json(entity_path)
    if not isinstance(cars, list):
        raise ValueError(f"{car_path} must contain a JSON list.")
    if not isinstance(entity_graph, dict) or "entities" not in entity_graph:
        raise ValueError(f"{entity_path} must contain an entity-centric graph with an 'entities' key.")

    print("=" * 60)
    print("Neo4j automotive graph importer")
    print("=" * 60)
    print(f"car_data:   {car_path} ({len(cars)} records)")
    print(f"entity_data:{entity_path} ({len(entity_graph.get('entities', {}))} entities)")
    print(f"neo4j:      {args.uri}")
    print(f"database:   {args.database}")

    db = Neo4jHttpClient(args.uri, args.user, args.password, args.database)

    if not args.no_clear:
        print("\nStep 1: clearing existing graph")
        db.run("MATCH (n) DETACH DELETE n")
        print("  cleared")
    else:
        print("\nStep 1: keeping existing graph")

    print("\nStep 2: creating constraints")
    create_constraints(db)
    print("  constraints ready")

    print("\nStep 3: importing car_data.json")
    car_stats = import_cars(db, cars)
    for key, value in car_stats.items():
        print(f"  {key}: {value}")

    print("\nStep 4: importing car_entity.json")
    entity_stats = import_entities(db, entity_graph)
    for key, value in entity_stats.items():
        print(f"  {key}: {value}")

    print_db_counts(db)
    print(f"\nDone. Neo4j Browser: {args.uri}")


if __name__ == "__main__":
    main()
