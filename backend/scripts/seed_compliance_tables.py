#!/usr/bin/env python3
"""
Seed the hs_tariffs, hs_pgas, and country_rules compliance tables.

Usage:
    cd backend
    source .venv/bin/activate
    DATABASE_URL=postgresql+psycopg://... python scripts/seed_compliance_tables.py

The script is idempotent: it deletes all rows from the 3 tables and re-inserts,
so it can be re-run safely when data changes.

Requires:
    - DATABASE_URL pointing to a Postgres instance
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make app imports work when running from the backend directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.database import CountryRule, HsPga, HsTariff, SessionLocal, WorldPort, engine, init_db

# ── Chapter descriptions for hs_tariffs ──────────────────────────────────────
_CHAPTER_DESCRIPTIONS: dict[str, str] = {
    "01": "Live animals",
    "02": "Meat and edible meat offal",
    "03": "Fish and crustaceans",
    "04": "Dairy produce; birds' eggs; natural honey",
    "05": "Products of animal origin NES",
    "06": "Live trees and other plants",
    "07": "Edible vegetables and roots",
    "08": "Edible fruit and nuts",
    "09": "Coffee, tea, maté and spices",
    "10": "Cereals",
    "11": "Products of the milling industry",
    "12": "Oil seeds and oleaginous fruits",
    "13": "Lac; gums, resins and other vegetable saps",
    "14": "Vegetable plaiting materials",
    "15": "Animal or vegetable fats and oils",
    "16": "Preparations of meat, fish or crustaceans",
    "17": "Sugars and sugar confectionery",
    "18": "Cocoa and cocoa preparations",
    "19": "Preparations of cereals, flour, starch or milk",
    "20": "Preparations of vegetables, fruit or nuts",
    "21": "Miscellaneous edible preparations",
    "22": "Beverages, spirits and vinegar",
    "23": "Residues and waste from the food industries",
    "25": "Salt; sulphur; earths and stone; plastering materials",
    "26": "Ores, slag and ash",
    "27": "Mineral fuels, mineral oils and products",
    "28": "Inorganic chemicals",
    "29": "Organic chemicals",
    "30": "Pharmaceutical products",
    "31": "Fertilisers",
    "32": "Tanning or dyeing extracts; dyes, pigments",
    "33": "Essential oils and resinoids; perfumery",
    "34": "Soap, organic surface-active agents, washing preparations",
    "35": "Albuminoidal substances; modified starches; glues",
    "36": "Explosives; pyrotechnic products",
    "37": "Photographic or cinematographic goods",
    "38": "Miscellaneous chemical products",
    "39": "Plastics and articles thereof",
    "40": "Rubber and articles thereof",
    "41": "Raw hides and skins (other than furskins) and leather",
    "42": "Articles of leather; saddlery and harness",
    "43": "Furskins and artificial fur; manufactures thereof",
    "44": "Wood and articles of wood; wood charcoal",
    "45": "Cork and articles of cork",
    "46": "Manufactures of straw, of esparto or of other plaiting materials",
    "47": "Pulp of wood or of other fibrous cellulosic material",
    "48": "Paper and paperboard",
    "49": "Printed books, newspapers, pictures and other products",
    "50": "Silk",
    "51": "Wool, fine or coarse animal hair",
    "52": "Cotton",
    "53": "Other vegetable textile fibres",
    "54": "Man-made filaments",
    "55": "Man-made staple fibres",
    "56": "Wadding, felt and nonwovens; special yarns",
    "57": "Carpets and other textile floor coverings",
    "58": "Special woven fabrics; tufted textile fabrics",
    "59": "Impregnated, coated, covered or laminated textile fabrics",
    "60": "Knitted or crocheted fabrics",
    "61": "Articles of apparel and clothing accessories, knitted",
    "62": "Articles of apparel and clothing accessories, not knitted",
    "63": "Other made-up textile articles; sets; worn clothing",
    "71": "Natural or cultured pearls, precious or semi-precious stones",
    "72": "Iron and steel",
    "73": "Articles of iron or steel",
    "74": "Copper and articles thereof",
    "75": "Nickel and articles thereof",
    "76": "Aluminium and articles thereof",
    "77": "Reserved for possible future use",
    "78": "Lead and articles thereof",
    "79": "Zinc and articles thereof",
    "80": "Tin and articles thereof",
    "81": "Other base metals; cermets; articles thereof",
    "82": "Tools, implements, cutlery, spoons and forks, of base metal",
    "83": "Miscellaneous articles of base metal",
    "84": "Nuclear reactors, boilers, machinery and mechanical appliances",
    "85": "Electrical machinery and equipment and parts thereof",
    "86": "Railway or tramway locomotives, rolling-stock and parts",
    "87": "Vehicles other than railway or tramway rolling-stock",
    "88": "Aircraft, spacecraft, and parts thereof",
    "89": "Ships, boats and floating structures",
    "90": "Optical, photographic, cinematographic, measuring instruments",
    "91": "Clocks and watches and parts thereof",
    "92": "Musical instruments; parts and accessories",
    "93": "Arms and ammunition; parts and accessories thereof",
}

# Reproduced from compliance.py constants
_CHAPTER_DUTY: dict[str, tuple[float, float]] = {
    **{str(i).zfill(2): (0.30, 0.05) for i in range(1, 4)},
    **{str(i).zfill(2): (0.30, 0.00) for i in range(4, 8)},
    **{str(i).zfill(2): (0.30, 0.05) for i in range(8, 15)},
    **{str(i).zfill(2): (0.100, 0.12) for i in range(15, 24)},
    **{str(i).zfill(2): (0.05, 0.05) for i in range(25, 28)},
    **{str(i).zfill(2): (0.075, 0.18) for i in range(28, 39)},
    **{str(i).zfill(2): (0.10, 0.18) for i in range(39, 41)},
    **{str(i).zfill(2): (0.10, 0.12) for i in range(41, 44)},
    **{str(i).zfill(2): (0.10, 0.12) for i in range(44, 47)},
    **{str(i).zfill(2): (0.10, 0.12) for i in range(47, 50)},
    **{str(i).zfill(2): (0.20, 0.12) for i in range(50, 64)},
    "71": (0.15, 0.03),
    **{str(i).zfill(2): (0.10, 0.18) for i in range(72, 84)},
    "84": (0.075, 0.18),
    "85": (0.15, 0.18),
    **{str(i).zfill(2): (0.125, 0.28) for i in range(86, 90)},
    "90": (0.05, 0.12),
    **{str(i).zfill(2): (0.10, 0.12) for i in range(91, 93)},
    "93": (0.00, 0.18),
}

BIS_MANDATORY_PREFIXES = [
    "8504", "8506", "8507", "8513", "8516", "8517",
    "8518", "8519", "8521", "8525", "8528", "8536",
    "8544", "9405",
]


def _build_hs_tariffs() -> list[dict]:
    rows = []
    for chapter, (bcd, igst) in _CHAPTER_DUTY.items():
        rows.append({
            "hs_code": chapter,
            "description": _CHAPTER_DESCRIPTIONS.get(chapter),
            "bcd_rate": bcd,
            "igst_rate": igst,
            "compensation_cess_rate": 0.0,
            "notes": None,
        })
    return rows


def _build_hs_pgas() -> list[dict]:
    rows: list[dict] = []

    # FSSAI — chapters 01–23
    for i in range(1, 24):
        prefix = str(i).zfill(2)
        rows.append({
            "hs_prefix": prefix,
            "agency": "FSSAI",
            "severity": "high",
            "title": "FSSAI import clearance required",
            "detail_template": (
                "HS {hs_code} is an article of food. The importer must submit prior intimation "
                "on FSSAI FIMS, the foreign manufacturer must be FSSAI-registered, and samples "
                "will be tested at a notified port lab before out-of-charge."
            ),
            "regulation_ref": "Food Safety and Standards (Import) Regulations 2017; FSSAI Circular",
            "is_active": True,
        })

    # Animal Quarantine — chapters 01–05
    for i in range(1, 6):
        prefix = str(i).zfill(2)
        rows.append({
            "hs_prefix": prefix,
            "agency": "ANIMAL_QUARANTINE",
            "severity": "high",
            "title": "Animal Quarantine clearance required",
            "detail_template": (
                "HS {hs_code} is an animal product. Import requires a No Objection Certificate "
                "from the Animal Quarantine and Certification Service (AQCS) and a health "
                "certificate from the country of export."
            ),
            "regulation_ref": "Prevention of Cruelty to Animals Act 1960; AQCS Import Guidelines",
            "is_active": True,
        })

    # Plant Quarantine — chapters 06–14
    for i in range(6, 15):
        prefix = str(i).zfill(2)
        rows.append({
            "hs_prefix": prefix,
            "agency": "PLANT_QUARANTINE",
            "severity": "high",
            "title": "Plant Quarantine clearance required",
            "detail_template": (
                "HS {hs_code} is a plant/plant product. Import requires a Phytosanitary "
                "Certificate from the country of export and clearance from the National Plant "
                "Protection Organisation (NPPO) at the port of entry."
            ),
            "regulation_ref": "Plant Quarantine (Regulation of Import into India) Order 2003",
            "is_active": True,
        })

    # CDSCO drugs — chapters 29 and 30
    for prefix in ("29", "30"):
        rows.append({
            "hs_prefix": prefix,
            "agency": "CDSCO_DRUGS",
            "severity": "high",
            "title": "CDSCO import licence required — drugs/pharmaceuticals",
            "detail_template": (
                "HS {hs_code} falls under pharmaceutical or chemical classification. Import of "
                "drugs requires a valid import licence from CDSCO (Central Drugs Standard "
                "Control Organisation) under the Drugs and Cosmetics Act."
            ),
            "regulation_ref": "Drugs and Cosmetics Act 1940; CDSCO Import Licence Guidelines",
            "is_active": True,
        })

    # CDSCO cosmetics — chapter 33
    rows.append({
        "hs_prefix": "33",
        "agency": "CDSCO_COSMETICS",
        "severity": "medium",
        "title": "CDSCO registration required — cosmetics",
        "detail_template": (
            "HS {hs_code} is a cosmetic product. Import requires CDSCO registration of the "
            "foreign manufacturer and compliance with the Cosmetics Rules 2020."
        ),
        "regulation_ref": "Drugs and Cosmetics Act 1940; Cosmetics Rules 2020",
        "is_active": True,
    })

    # CDSCO medical devices — chapter 90
    rows.append({
        "hs_prefix": "90",
        "agency": "CDSCO_MEDICAL_DEVICES",
        "severity": "high",
        "title": "CDSCO registration required — medical devices",
        "detail_template": (
            "HS {hs_code} may be a medical device. Import requires CDSCO registration. "
            "Class A/B devices need manufacturing site registration; Class C/D require "
            "product registration."
        ),
        "regulation_ref": "Medical Devices Rules 2017; CDSCO Import Guidelines",
        "is_active": True,
    })

    # AERB — prefix 2844
    rows.append({
        "hs_prefix": "2844",
        "agency": "AERB",
        "severity": "critical",
        "title": "AERB authorisation required — radioactive material",
        "detail_template": (
            "HS {hs_code} contains radioactive isotopes. Import requires prior consent from "
            "the Atomic Energy Regulatory Board (AERB) under the Atomic Energy Act."
        ),
        "regulation_ref": "Atomic Energy Act 1962; AERB Import Guidelines",
        "is_active": True,
    })

    # BIS CRS — 14 prefixes
    for prefix in BIS_MANDATORY_PREFIXES:
        rows.append({
            "hs_prefix": prefix,
            "agency": "BIS_CRS",
            "severity": "high",
            "title": "BIS Compulsory Registration required",
            "detail_template": (
                "HS {hs_code} falls under BIS CRS. A valid BIS licence for this "
                "manufacturer/brand is required before customs release. Non-compliant goods "
                "are liable to seizure under the BIS Act 2016."
            ),
            "regulation_ref": "BIS (Compulsory Registration) Order 2012; BIS Act 2016 Section 17",
            "is_active": True,
        })

    # BIS QCO Steel — chapter 72
    rows.append({
        "hs_prefix": "72",
        "agency": "BIS_QCO_STEEL",
        "severity": "high",
        "title": "BIS Quality Control Order — steel products",
        "detail_template": (
            "HS {hs_code} is a steel product covered by BIS Quality Control Orders. Import "
            "requires a BIS Standard Mark or conformity certificate. Non-compliant steel is "
            "prohibited entry."
        ),
        "regulation_ref": "BIS QCO for Steel; Ministry of Steel notifications",
        "is_active": True,
    })

    # BIS QCO Toys — chapter 95
    rows.append({
        "hs_prefix": "95",
        "agency": "BIS_QCO_TOYS",
        "severity": "high",
        "title": "BIS Quality Control Order — toys",
        "detail_template": (
            "HS {hs_code} is a toy covered by the BIS QCO for Toys. All imported toys must "
            "bear the ISI mark or BIS CoC. Import of non-compliant toys is banned."
        ),
        "regulation_ref": "Toys (Quality Control) Order 2020; BIS Act 2016",
        "is_active": True,
    })

    # WPC — prefixes 8517, 8525, 8527
    for prefix in ("8517", "8525", "8527"):
        rows.append({
            "hs_prefix": prefix,
            "agency": "WPC",
            "severity": "medium",
            "title": "WPC licence required — wireless equipment",
            "detail_template": (
                "HS {hs_code} contains wireless/radio capability. A WPC import licence from "
                "the Ministry of Communications is required prior to import."
            ),
            "regulation_ref": "Indian Wireless Telegraphy Act 1933; WPC Licensing Guidelines",
            "is_active": True,
        })

    # SCOMET — prefixes 9301–9306
    for prefix in ("9301", "9302", "9303", "9304", "9305", "9306"):
        rows.append({
            "hs_prefix": prefix,
            "agency": "SCOMET",
            "severity": "critical",
            "title": "SCOMET item — DGFT authorisation required",
            "detail_template": (
                "HS {hs_code} is a SCOMET item. Import requires DGFT authorisation, end-use "
                "and end-user certificate, and post-import reporting. Unauthorised import is "
                "a criminal offence."
            ),
            "regulation_ref": (
                "DGFT SCOMET List, Appendix 3, Schedule 2 ITC-HS; FTDR Act 1992"
            ),
            "is_active": True,
        })

    return rows


def _build_country_rules() -> list[dict]:
    rows: list[dict] = []

    # Restricted / sanctioned / high-risk origins
    restricted = [
        {
            "country_name": "pakistan",
            "rule_type": "RESTRICTED",
            "severity": "critical",
            "title": "Import from Pakistan — trade suspended",
            "detail": (
                "Direct import of goods from Pakistan is not permitted following India's "
                "revocation of MFN status in February 2019. A specific DGFT exemption order "
                "is required before this shipment can be filed."
            ),
            "regulation_ref": "DGFT Public Notice 2/2019",
            "fta_name": None,
        },
        {
            "country_name": "north korea",
            "rule_type": "SANCTIONED",
            "severity": "critical",
            "title": "UN-sanctioned country",
            "detail": (
                "Goods originating in North Korea are subject to mandatory UN Security Council "
                "sanctions that India is obliged to implement. Escalate to DGFT and Ministry "
                "of External Affairs before proceeding."
            ),
            "regulation_ref": "UNSC Resolutions; Customs Act 1962 S.11",
            "fta_name": None,
        },
        {
            "country_name": "iran",
            "rule_type": "HIGH_RISK",
            "severity": "high",
            "title": "High-risk origin: Iran",
            "detail": (
                "Shipments from Iran require enhanced due diligence. Verify end-use, confirm "
                "no sanctions exposure, and retain detailed supplier documentation for "
                "potential customs examination."
            ),
            "regulation_ref": "Customs Act 1962 S.11; DGFT FTP 2023",
            "fta_name": None,
        },
        {
            "country_name": "myanmar",
            "rule_type": "HIGH_RISK",
            "severity": "high",
            "title": "High-risk origin: Myanmar",
            "detail": (
                "Shipments from Myanmar require enhanced due diligence. Verify end-use, confirm "
                "no sanctions exposure, and retain detailed supplier documentation for "
                "potential customs examination."
            ),
            "regulation_ref": "Customs Act 1962 S.11; DGFT FTP 2023",
            "fta_name": None,
        },
    ]
    for r in restricted:
        rows.append({**r, "is_active": True})

    # FTA countries
    ftas = [
        ("uae", "India-UAE CEPA 2022"),
        ("saudi arabia", "India-GCC FTA (under negotiation — partial)"),
        ("thailand", "ASEAN-India FTA (AIFTA)"),
        ("vietnam", "ASEAN-India FTA (AIFTA)"),
        ("malaysia", "ASEAN-India FTA (AIFTA)"),
        ("indonesia", "ASEAN-India FTA (AIFTA)"),
        ("philippines", "ASEAN-India FTA (AIFTA)"),
        ("singapore", "ASEAN-India FTA (AIFTA)"),
        ("japan", "India-Japan CEPA (IJCEPA)"),
        ("south korea", "India-Korea CEPA"),
        ("sri lanka", "India-Sri Lanka FTA (ISFTA)"),
        ("australia", "India-Australia ECTA 2022"),
        ("mauritius", "India-Mauritius CECPA"),
    ]
    for country, fta_name in ftas:
        rows.append({
            "country_name": country,
            "rule_type": "FTA",
            "severity": "low",
            "title": f"Preferential duty available — {fta_name}",
            "detail": (
                f"Goods originating in {country.title()} may qualify for reduced BCD under "
                f"{fta_name}. Obtain a valid Certificate of Origin (CoO) in the prescribed "
                f"format and declare preferential claim at filing."
            ),
            "regulation_ref": fta_name,
            "fta_name": fta_name,
            "is_active": True,
        })

    return rows


def _build_world_ports() -> list[dict]:
    # ── Indian entry ports ────────────────────────────────────────────────────
    indian_ports = [
        ("INJNP", "Jawaharlal Nehru Port (JNPT)", "Nhava Sheva", "India", "IN", "South Asia", "SEA",  True,  "SCRUTINY", "Highest-volume container port in India; highest RMS examination selection rate.", 6.0),
        ("INMUN", "Mundra Port",                  "Mundra",       "India", "IN", "South Asia", "SEA",  True,  "HIGH",     "Rapid-growth port; enhanced scrutiny on under-valuation and mis-declaration.", 7.0),
        ("INMAA", "Chennai Port",                 "Chennai",      "India", "IN", "South Asia", "SEA",  True,  "HIGH",     "Primary gateway for South India; elevated examination rates for electronics and textiles.", 1.5),
        ("INCCU", "Kolkata Port / Haldia",         "Kolkata",      "India", "IN", "South Asia", "SEA",  True,  "HIGH",     "Eastern gateway; high scrutiny on goods via Bangladesh/Myanmar corridor.", 0.8),
        ("INVTZ", "Visakhapatnam Port",            "Visakhapatnam","India", "IN", "South Asia", "SEA",  True,  "MEDIUM",   None, 0.6),
        ("INCOK", "Cochin Port",                   "Kochi",        "India", "IN", "South Asia", "SEA",  True,  "MEDIUM",   None, 0.5),
        ("INKLA", "Kandla Port / Deendayal",       "Kandla",       "India", "IN", "South Asia", "SEA",  True,  "MEDIUM",   None, 1.0),
        ("INPAV", "Pipavav Port",                  "Pipavav",      "India", "IN", "South Asia", "SEA",  True,  "MEDIUM",   None, 0.7),
        ("INDEL", "Delhi ICD Tughlakabad",          "Delhi",        "India", "IN", "South Asia", "ICD",  True,  "HIGH",     "Largest inland container depot; high examination rate for containerised imports.", None),
        ("INDPP", "Delhi ICD Patparganj",           "Delhi",        "India", "IN", "South Asia", "ICD",  True,  "HIGH",     None, None),
        ("INBLR", "Bangalore ICD",                 "Bengaluru",    "India", "IN", "South Asia", "ICD",  True,  "MEDIUM",   None, None),
        ("INHYD", "Hyderabad ICD",                 "Hyderabad",    "India", "IN", "South Asia", "ICD",  True,  "MEDIUM",   None, None),
        ("INBOM", "Mumbai Air Cargo Complex",       "Mumbai",       "India", "IN", "South Asia", "AIR",  True,  "HIGH",     "Primary air cargo gateway; high examination rate for express and courier consignments.", None),
        ("INDEL2","Delhi Air Cargo / IGI",         "Delhi",        "India", "IN", "South Asia", "AIR",  True,  "HIGH",     None, None),
    ]

    # ── Top global ports (by TEU and trade relevance to India) ───────────────
    # Top 5 globally + key regional hubs
    global_ports = [
        # East Asia — Top 5 global by TEU
        ("CNSHA", "Port of Shanghai",          "Shanghai",    "China",       "CN", "East Asia",    "SEA", False, "MEDIUM", "World's largest port. Verify ITC-HS classification; Chinese electronics subject to BIS/WPC checks.", 47.0),
        ("CNNBO", "Port of Ningbo-Zhoushan",   "Ningbo",      "China",       "CN", "East Asia",    "SEA", False, "MEDIUM", "Second-largest globally; major machinery and electronics exporter to India.", 33.0),
        ("CNSZX", "Port of Shenzhen (Yantian)","Shenzhen",    "China",       "CN", "East Asia",    "SEA", False, "MEDIUM", "Electronics manufacturing hub. High volume of BIS/WPC-regulated goods.", 30.0),
        ("CNGZU", "Port of Guangzhou/Nansha",  "Guangzhou",   "China",       "CN", "East Asia",    "SEA", False, "MEDIUM", None, 25.0),
        ("CNTAO", "Port of Qingdao",           "Qingdao",     "China",       "CN", "East Asia",    "SEA", False, "MEDIUM", None, 25.0),
        # Southeast Asia
        ("SGSIN", "Port of Singapore",         "Singapore",   "Singapore",   "SG", "Southeast Asia","SEA", False, "LOW",    "Major transshipment hub. Verify actual origin on transhipped cargo to avoid mis-declaration.", 37.0),
        ("MYKLG", "Port of Klang",             "Klang",       "Malaysia",    "MY", "Southeast Asia","SEA", False, "LOW",    None, 14.0),
        ("VNHPH", "Port of Hai Phong",         "Hai Phong",   "Vietnam",     "VN", "Southeast Asia","SEA", False, "LOW",    None, 5.0),
        # South Asia
        ("LKCMB", "Port of Colombo",           "Colombo",     "Sri Lanka",   "LK", "South Asia",   "SEA", False, "MEDIUM", "Primary South Asian transshipment hub. Verify CoO — goods transhipped here may misrepresent origin.", 7.0),
        ("PKKAR", "Port of Karachi",           "Karachi",     "Pakistan",    "PK", "South Asia",   "SEA", False, "HIGH",   "Pakistan port — India-Pakistan trade is suspended. Goods routed via Karachi require DGFT exemption.", 1.5),
        # Middle East
        ("AEJEA", "Port of Jebel Ali",         "Dubai",       "UAE",         "AE", "Middle East",  "SEA", False, "LOW",    "Largest port in Middle East; major re-export hub. Verify actual origin for UAE CEPA benefit claims.", 15.0),
        ("SAJET", "Jeddah Islamic Port",        "Jeddah",      "Saudi Arabia","SA", "Middle East",  "SEA", False, "LOW",    None, 5.0),
        # Europe
        ("NLRTM", "Port of Rotterdam",         "Rotterdam",   "Netherlands", "NL", "Europe",       "SEA", False, "LOW",    "Europe's largest port and major gateway for European exports to India.", 14.0),
        ("DEHAM", "Port of Hamburg",           "Hamburg",     "Germany",     "DE", "Europe",       "SEA", False, "LOW",    None, 8.0),
        ("BEANR", "Port of Antwerp-Bruges",    "Antwerp",     "Belgium",     "BE", "Europe",       "SEA", False, "LOW",    None, 12.0),
        # Americas
        ("USLAX", "Port of Los Angeles",       "Los Angeles", "USA",         "US", "Americas",     "SEA", False, "LOW",    None, 10.0),
        ("USNYC", "Port of New York/New Jersey","New York",   "USA",         "US", "Americas",     "SEA", False, "LOW",    None, 9.0),
        # Northeast Asia
        ("KRBSN", "Port of Busan",             "Busan",       "South Korea", "KR", "Northeast Asia","SEA", False, "LOW",   None, 22.0),
        ("JPYOK", "Port of Yokohama",          "Yokohama",    "Japan",       "JP", "Northeast Asia","SEA", False, "LOW",   None, 3.0),
    ]

    rows = []
    for p in indian_ports + global_ports:
        rows.append(dict(
            port_code=p[0], port_name=p[1], city=p[2], country=p[3],
            country_code=p[4], region=p[5], port_type=p[6],
            is_indian_entry_port=p[7], risk_level=p[8],
            risk_note=p[9], annual_teu_millions=p[10],
        ))
    return rows


async def seed() -> None:
    if not engine:
        print("ERROR: DATABASE_URL is not set. Set it before running this script.")
        sys.exit(1)

    await init_db()

    if not SessionLocal:
        print("ERROR: Database session could not be created.")
        sys.exit(1)

    hs_tariffs = _build_hs_tariffs()
    hs_pgas = _build_hs_pgas()
    country_rules = _build_country_rules()
    world_ports = _build_world_ports()

    async with SessionLocal() as session:
        async with session.begin():
            # Idempotent: delete all rows first
            await session.execute(text("DELETE FROM world_ports"))
            await session.execute(text("DELETE FROM country_rules"))
            await session.execute(text("DELETE FROM hs_pgas"))
            await session.execute(text("DELETE FROM hs_tariffs"))
            print("Cleared existing rows from all compliance tables.")

            for row in hs_tariffs:
                session.add(HsTariff(**row))
            print(f"  Inserting {len(hs_tariffs)} hs_tariffs rows.")

            for row in hs_pgas:
                session.add(HsPga(**row))
            print(f"  Inserting {len(hs_pgas)} hs_pgas rows.")

            for row in country_rules:
                session.add(CountryRule(**row))
            print(f"  Inserting {len(country_rules)} country_rules rows.")

            for row in world_ports:
                session.add(WorldPort(**row))
            print(f"  Inserting {len(world_ports)} world_ports rows.")

    print(
        f"\nDone. Seeded {len(hs_tariffs)} tariff rows, "
        f"{len(hs_pgas)} PGA rows, "
        f"{len(country_rules)} country rule rows, "
        f"{len(world_ports)} world port rows."
    )


if __name__ == "__main__":
    asyncio.run(seed())
