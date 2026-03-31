import importlib.util
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "etl" / "04_load_lookups.py"


def load_lookup_module():
    spec = importlib.util.spec_from_file_location("load_lookups_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


load_lookups = load_lookup_module()


def make_local_tmp_dir():
    tmp_dir = PROJECT_ROOT / ".tmp" / f"test_load_lookups_{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


def cleanup_local_tmp_dir(tmp_dir):
    for path in sorted(tmp_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            path.rmdir()


def test_load_conference_source_rows_merges_flat_and_raw_sources():
    tmp_path = make_local_tmp_dir()
    flat_csv = tmp_path / "iCore26_KilledColumnsForLoading.csv"
    raw_dir = tmp_path / "iCORE_raw"
    raw_dir.mkdir()

    try:
        flat_csv.write_text(
            "\n".join(
                [
                    "ID, Title,Acronym,Source,Rank,DBLP,PrimaryFoR",
                    "1,Conference Alpha,CONF,ICORE2026,A,Yes,4601",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (raw_dir / "CORE-4601.csv").write_text(
            "\n".join(
                [
                    "1,Conference Alpha,CONF,ICORE2026,A,Yes,4601,4611",
                    "2,Conference Beta,BETA,ICORE2026,B,Yes,4606",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        source_rows, stats = load_lookups.load_conference_source_rows(
            {"4601", "4606", "4611"},
            conference_csv=flat_csv,
            raw_dir=raw_dir,
        )

        by_acronym = {row["acronym"]: row for row in source_rows}

        assert set(by_acronym) == {"CONF", "BETA"}
        assert by_acronym["CONF"]["primary_for"] == "4601"
        assert by_acronym["BETA"]["primary_for"] == "4606"
        assert stats["flat_rows_read"] == 1
        assert stats["raw_rows_read"] == 2
        assert stats["both_source_rows"] == 1
        assert stats["raw_only_rows"] == 1
    finally:
        cleanup_local_tmp_dir(tmp_path)


def test_load_conference_source_rows_falls_back_to_raw_for_valid_primary_for():
    tmp_path = make_local_tmp_dir()
    flat_csv = tmp_path / "iCore26_KilledColumnsForLoading.csv"
    raw_dir = tmp_path / "iCORE_raw"
    raw_dir.mkdir()

    try:
        flat_csv.write_text(
            "\n".join(
                [
                    "ID, Title,Acronym,Source,Rank,DBLP,PrimaryFoR",
                    "9,Conference Rescue,RESCUE,ICORE2026,B,Yes,National: USA",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (raw_dir / "CORE-4606.csv").write_text(
            "\n".join(
                [
                    "ID,Title,Acronym,Source,Rank,DBLP,PrimaryFoR,FoR2",
                    "9,Conference Rescue,RESCUE,ICORE2026,B,Yes,4606,4613",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        source_rows, stats = load_lookups.load_conference_source_rows(
            {"4606", "4613"},
            conference_csv=flat_csv,
            raw_dir=raw_dir,
        )

        assert source_rows == [
            {
                "line_no": 2,
                "acronym": "RESCUE",
                "title": "Conference Rescue",
                "title_key": load_lookups.normalize_conference_title_fingerprint("Conference Rescue"),
                "rank": "B",
                "primary_for": "4606",
            }
        ]
        assert stats["invalid_primary_for_rows"] == 0
    finally:
        cleanup_local_tmp_dir(tmp_path)
