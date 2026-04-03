from __future__ import annotations

from app.config import settings
from app.observability import logger
from app.schemas import ComplianceIssue, ComplianceResult, DutyEstimate, ExtractedShipmentData
from app.database import CountryRule, HsPga, HsTariff, SessionLocal, WorldPort
from sqlalchemy import select

# ── Origin restrictions ───────────────────────────────────────────────────────
# India suspended bilateral trade with Pakistan and revoked MFN status in Feb 2019.
# Direct imports from Pakistan require a specific DGFT exemption order.
RESTRICTED_ORIGINS = {"pakistan"}

# India is bound by UN Security Council Chapter VII mandatory sanctions.
UN_SANCTIONED_ORIGINS = {"north korea"}

# Iran: India follows UN sanctions; trade requires government-level clearance.
HIGH_RISK_ORIGINS = {"iran", "myanmar"}

# ── BIS Compulsory Registration Scheme (CRS) ─────────────────────────────────
# Electronics and electrical goods that require BIS registration before customs
# release. Importers must hold a valid BIS licence for the manufacturer/brand.
# Source: BIS (Compulsory Registration) Order 2012 and subsequent amendments.
BIS_MANDATORY_PREFIXES = {
    "8504",  # Transformers, adaptors, chargers
    "8506",  # Primary batteries
    "8507",  # Accumulators / lithium batteries
    "8513",  # Portable electric lamps
    "8516",  # Water heaters, hair dryers, irons
    "8517",  # Telephones including smartphones
    "8518",  # Microphones, loudspeakers, headphones
    "8519",  # Sound recording / reproducing apparatus
    "8521",  # Video recording / reproducing apparatus
    "8525",  # Transmission apparatus (WiFi routers, Bluetooth, cameras)
    "8528",  # Monitors, projectors, televisions
    "8536",  # Switches, fuses, connectors
    "8544",  # Insulated wire, cable, optical fibre cable
    "9405",  # LED luminaires and lighting fittings
}

# ── FSSAI — all food, beverage, and agricultural imports ─────────────────────
# Any article of food imported into India requires FSSAI clearance (prior
# intimation, testing at notified labs, registration of foreign manufacturer).
# Source: Food Safety and Standards (Import) Regulations 2017.
# HS chapters 01–23 broadly cover food and agricultural commodities.
FSSAI_CHAPTERS = {str(i).zfill(2) for i in range(1, 24)}

# ── WPC Licence — radio / wireless equipment ─────────────────────────────────
# Wireless devices require a WPC licence from the Ministry of Communications
# before import. Covers WiFi, Bluetooth, cellular modules, satellite equipment.
# Source: Indian Wireless Telegraphy Act 1933 / WPC licensing guidelines.
WPC_PREFIXES = {"8517", "8525", "8527"}

# ── SCOMET — dual-use, defence, and space items ───────────────────────────────
# Special Chemicals, Organisms, Materials, Equipment and Technologies.
# Imports require DGFT authorisation; end-use and end-user certification needed.
# Source: DGFT SCOMET list (Appendix 3, Schedule 2, ITC-HS).
SCOMET_PREFIXES = {"9301", "9302", "9303", "9304", "9305", "9306"}

# ── High-scrutiny ports ───────────────────────────────────────────────────────
# Ports historically associated with higher examination selection rates.
HIGH_SCRUTINY_PORTS = {
    "jnpt", "nhava sheva", "chennai", "kolkata",
    "delhi icd", "tughlakabad", "patparganj",
}

SEVERITY_WEIGHT = {"critical": 35, "high": 20, "medium": 10, "low": 5}

# ── Indicative duty rates by ITC-HS chapter ──────────────────────────────────
# Tuple: (BCD rate, IGST rate). These are chapter-level approximations.
# Actual rates require a full CBIC tariff schedule lookup.
_CHAPTER_DUTY: dict[str, tuple[float, float]] = {
    **{str(i).zfill(2): (0.30, 0.05) for i in range(1, 4)},    # Live animals, meat, fish
    **{str(i).zfill(2): (0.30, 0.00) for i in range(4, 8)},    # Dairy, eggs, honey, vegetables
    **{str(i).zfill(2): (0.30, 0.05) for i in range(8, 15)},   # Fruit, cereals, milling
    **{str(i).zfill(2): (0.100, 0.12) for i in range(15, 24)}, # Fats, prepared food, beverages
    **{str(i).zfill(2): (0.05, 0.05) for i in range(25, 28)},  # Minerals, fuels
    **{str(i).zfill(2): (0.075, 0.18) for i in range(28, 39)}, # Chemicals
    **{str(i).zfill(2): (0.10, 0.18) for i in range(39, 41)},  # Plastics, rubber
    **{str(i).zfill(2): (0.10, 0.12) for i in range(41, 44)},  # Hides, leather, fur
    **{str(i).zfill(2): (0.10, 0.12) for i in range(44, 47)},  # Wood, cork
    **{str(i).zfill(2): (0.10, 0.12) for i in range(47, 50)},  # Pulp, paper
    **{str(i).zfill(2): (0.20, 0.12) for i in range(50, 64)},  # Textiles and apparel
    "71": (0.15, 0.03),                                         # Precious metals, jewellery
    **{str(i).zfill(2): (0.10, 0.18) for i in range(72, 84)},  # Base metals
    "84": (0.075, 0.18),                                        # Machinery, mechanical appliances
    "85": (0.15, 0.18),                                         # Electronics (simplified; varies by item)
    **{str(i).zfill(2): (0.125, 0.28) for i in range(86, 90)}, # Vehicles and transport equipment
    "90": (0.05, 0.12),                                         # Optical, medical instruments
    **{str(i).zfill(2): (0.10, 0.12) for i in range(91, 93)},  # Clocks, musical instruments
    "93": (0.00, 0.18),                                         # Weapons (heavily restricted; duty is moot)
}
_DEFAULT_DUTY = (0.10, 0.18)  # BCD 10%, IGST 18%


# ── Duty estimation ───────────────────────────────────────────────────────────

def _estimate_duty(data: ExtractedShipmentData) -> DutyEstimate:
    """Indicative landed-duty estimate using chapter-level rates.

    Formula (Customs Tariff Act 1975 + IGST Act 2017):
        Assessable Value = CIF value in INR
        BCD = Assessable Value × BCD_rate
        SWS = BCD × 10%   (Social Welfare Surcharge)
        IGST = (Assessable Value + BCD + SWS) × IGST_rate
        Total = BCD + SWS + IGST
    """
    assessable_inr = data.shipment_value_usd * settings.usd_to_inr

    chapter = (data.items[0].hs_code[:2] if data.items else "00")
    bcd_rate, igst_rate = _CHAPTER_DUTY.get(chapter, _DEFAULT_DUTY)

    bcd = assessable_inr * bcd_rate
    sws = bcd * 0.10
    igst = (assessable_inr + bcd + sws) * igst_rate
    total = bcd + sws + igst
    effective_pct = (total / assessable_inr * 100) if assessable_inr > 0 else 0.0

    return DutyEstimate(
        assessable_value_inr=round(assessable_inr, 2),
        bcd_inr=round(bcd, 2),
        sws_inr=round(sws, 2),
        igst_inr=round(igst, 2),
        total_duty_inr=round(total, 2),
        effective_rate_pct=round(effective_pct, 2),
        note=(
            "Indicative chapter-level estimate only. "
            "Verify against the current CBIC Basic Customs Duty schedule and "
            "applicable exemption notifications before filing."
        ),
    )


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _compute_result(
    issues: list[ComplianceIssue],
    suggestions: list[str],
    total_penalty: int,
    has_critical: bool,
    duty_estimate: DutyEstimate,
) -> ComplianceResult:
    score = max(0, 100 - total_penalty)

    if has_critical:
        risk_level = "Critical"
        clearance_prediction = "Manual hold — regulatory authorisation required"
    elif score < 55:
        risk_level = "High"
        clearance_prediction = "Second appraisement or examination expected"
    elif score < 80:
        risk_level = "Moderate"
        clearance_prediction = "Clearance likely with document corrections"
    else:
        risk_level = "Low"
        clearance_prediction = "Likely to clear under first appraisement"

    base_suggestions = [
        "Verify ITC-HS classification to 8-digit level before filing the Bill of Entry.",
        "Ensure commercial invoice, packing list, and Bill of Lading are consistent and signed.",
        "Confirm IEC (Importer Exporter Code) is active and linked to the ICEGATE profile.",
    ]
    if issues:
        base_suggestions.insert(0, "Resolve all flagged compliance issues before submitting to ICEGATE.")

    return ComplianceResult(
        score=score,
        clearance_prediction=clearance_prediction,
        risk_level=risk_level,
        issues=issues,
        suggestions=suggestions + base_suggestions,
        duty_estimate=duty_estimate,
    )


def _shipment_level_checks(
    data: ExtractedShipmentData,
    skip_port_check: bool = False,
) -> tuple[list[ComplianceIssue], int]:
    """Checks that are purely shipment-level and don't need DB lookup."""
    issues: list[ComplianceIssue] = []
    total_penalty = 0

    if data.shipment_value_usd >= 10000:
        issues.append(ComplianceIssue(
            severity="medium",
            title="High-value shipment — enhanced scrutiny likely",
            detail=(
                f"Shipments valued above USD 10,000 (approx INR "
                f"{data.shipment_value_usd * settings.usd_to_inr:,.0f}) attract closer "
                f"examination by the customs appraising group. Ensure all supporting "
                f"documents (invoice, packing list, bank remittance advice) are consistent "
                f"and match the declared transaction value."
            ),
            regulation="Customs Valuation Rules 2007; Customs Act 1962 Section 14",
        ))
        total_penalty += SEVERITY_WEIGHT["medium"]

    if data.incoterm.upper() not in {"CIF", "CIP"}:
        issues.append(ComplianceIssue(
            severity="low",
            title="Non-CIF incoterm — valuation adjustment required",
            detail=(
                f"Indian customs duty is assessed on CIF (Cost + Insurance + Freight) value "
                f"under Rule 3 of the Customs Valuation Rules 2007. The declared incoterm is "
                f"{data.incoterm}. The importer or broker must add freight and insurance "
                f"costs to the invoice value to arrive at the correct assessable value."
            ),
            regulation="Customs Valuation (Determination of Value of Imported Goods) Rules 2007",
        ))
        total_penalty += SEVERITY_WEIGHT["low"]

    if not skip_port_check and data.port_of_entry.lower() in HIGH_SCRUTINY_PORTS:
        issues.append(ComplianceIssue(
            severity="low",
            title="High-scrutiny port — allow for examination delay",
            detail=(
                f"{data.port_of_entry} is a high-volume port with elevated examination "
                f"selection rates. Factor 3–7 additional working days into clearance timeline "
                f"for possible Dock Examination or Second Appraisement."
            ),
            regulation="CBIC Risk Management System; Port Examination Guidelines",
        ))
        total_penalty += SEVERITY_WEIGHT["low"]

    return issues, total_penalty


def _check_port_db(port_of_entry: str, ports: list[WorldPort]) -> tuple[list[ComplianceIssue], int]:
    """Match port_of_entry against world_ports table and generate compliance issues."""
    port_lower = port_of_entry.lower().strip()
    if not port_lower or port_lower == "unknown":
        return [], 0

    matched: WorldPort | None = None
    for port in ports:
        if port.city.lower() in port_lower or port.port_name.lower() in port_lower:
            matched = port
            break

    if not matched:
        return [], 0

    issues: list[ComplianceIssue] = []
    penalty = 0

    if matched.is_indian_entry_port:
        if matched.risk_level in ("SCRUTINY", "HIGH"):
            note = f" {matched.risk_note}" if matched.risk_note else ""
            issues.append(ComplianceIssue(
                severity="low",
                title=f"High-scrutiny port — {matched.port_name}",
                detail=(
                    f"{matched.port_name} ({matched.city}) is a high-volume Indian customs port "
                    f"with elevated RMS examination selection rates.{note} "
                    f"Factor 3–7 additional working days into the clearance timeline."
                ),
                regulation="CBIC Risk Management System; Port Examination Guidelines",
            ))
            penalty += SEVERITY_WEIGHT["low"]
    else:
        if matched.risk_level in ("MEDIUM", "HIGH"):
            note = matched.risk_note or "Verify Certificate of Origin to confirm actual country of manufacture."
            issues.append(ComplianceIssue(
                severity="low",
                title=f"Origin port note — {matched.port_name}",
                detail=(
                    f"{matched.port_name} ({matched.city}, {matched.country}) "
                    f"is a major international port. {note}"
                ),
                regulation="Customs Act 1962 Section 14; Customs Valuation Rules 2007",
            ))
            penalty += SEVERITY_WEIGHT["low"]

    return issues, penalty


# ── DB-backed evaluation ──────────────────────────────────────────────────────

async def _evaluate_from_db(data: ExtractedShipmentData) -> ComplianceResult:
    """Evaluate compliance. Uses in-process cache when warm (zero DB queries);
    falls back to live DB queries when cache is cold (first request after restart)."""
    from app import cache as _cache

    issues: list[ComplianceIssue] = []
    suggestions: list[str] = []
    total_penalty = 0
    has_critical = False

    # Collect all HS prefixes and origins
    all_prefixes: set[str] = set()
    origins: set[str] = set()
    for item in data.items:
        hs = item.hs_code.strip()
        for length in (2, 4, 6, 8):
            if len(hs) >= length:
                all_prefixes.add(hs[:length])
        origins.add(item.country_of_origin.lower().strip())

    if _cache.is_warm():
        # ── Cache path: zero DB queries ───────────────────────────────────────
        pga_rows: list[HsPga] = [p for p in _cache.get_pgas() if p.hs_prefix in all_prefixes]
        country_rows: list[CountryRule] = [r for r in _cache.get_country_rules() if r.country_name in origins]
        world_ports: list[WorldPort] = _cache.get_ports()
        tariff_map: dict[str, HsTariff] = _cache.get_tariff_map()
    else:
        # ── DB path: cache not yet warm (first request after cold start) ──────
        async with SessionLocal() as session:  # type: ignore[misc]
            pga_rows = list((await session.execute(
                select(HsPga).where(HsPga.hs_prefix.in_(list(all_prefixes))).where(HsPga.is_active.is_(True))
            )).scalars().all())

            country_rows = list((await session.execute(
                select(CountryRule).where(CountryRule.country_name.in_(list(origins))).where(CountryRule.is_active.is_(True))
            )).scalars().all())

            hs_codes_8 = [item.hs_code.strip() for item in data.items if len(item.hs_code.strip()) >= 8]
            chapters = [item.hs_code.strip()[:2] for item in data.items]
            lookup_codes = list({*hs_codes_8, *chapters})
            tariff_map = {row.hs_code: row for row in (await session.execute(
                select(HsTariff).where(HsTariff.hs_code.in_(lookup_codes))
            )).scalars().all()}

            world_ports = list((await session.execute(
                select(WorldPort).where(WorldPort.is_active.is_(True))
            )).scalars().all())

    # ── Per-item checks ───────────────────────────────────────────────────────
    for item in data.items:
        hs = item.hs_code.strip()

        # PGA issues — deduplicate per agency for this item
        seen_agencies: set[str] = set()
        for pga in pga_rows:
            # Check if pga.hs_prefix is a prefix of this item's hs_code
            if not hs.startswith(pga.hs_prefix):
                continue
            if pga.agency in seen_agencies:
                continue
            seen_agencies.add(pga.agency)

            detail = pga.detail_template.replace("{hs_code}", hs)
            issue = ComplianceIssue(
                severity=pga.severity,
                title=pga.title,
                detail=detail,
                regulation=pga.regulation_ref,
            )
            issues.append(issue)
            penalty = SEVERITY_WEIGHT.get(pga.severity.lower(), 5)
            total_penalty += penalty
            if pga.severity.lower() == "critical":
                has_critical = True

        # Incomplete HS code check (shipment-level but per item)
        if len(hs) < 8:
            issues.append(ComplianceIssue(
                severity="medium",
                title="Incomplete ITC-HS code",
                detail=(
                    f"India uses 8-digit ITC-HS codes for Bill of Entry filing. The declared "
                    f"code '{hs}' has only {len(hs)} digit(s). An incomplete code prevents "
                    f"accurate duty assessment and may cause the Bill of Entry to be rejected "
                    f"at ICEGATE."
                ),
                regulation="Customs Tariff Act 1975; CBIC Circular on ITC-HS classification",
            ))
            total_penalty += SEVERITY_WEIGHT["medium"]

        # Origin / country rule checks
        origin = item.country_of_origin.lower().strip()
        for rule in country_rows:
            if rule.country_name != origin:
                continue
            if rule.rule_type == "FTA":
                suggestions.append(
                    f"{rule.title}: {rule.detail}"
                )
            else:
                issue = ComplianceIssue(
                    severity=rule.severity,
                    title=rule.title,
                    detail=rule.detail,
                    regulation=rule.regulation_ref,
                )
                issues.append(issue)
                penalty = SEVERITY_WEIGHT.get(rule.severity.lower(), 5)
                total_penalty += penalty
                if rule.severity.lower() == "critical":
                    has_critical = True

    # ── Shipment-level checks (value, incoterm; port handled via DB below) ──────
    shipment_issues, shipment_penalty = _shipment_level_checks(data, skip_port_check=True)
    issues.extend(shipment_issues)
    total_penalty += shipment_penalty

    # ── Port-of-entry check via world_ports table ─────────────────────────────
    port_issues, port_penalty = _check_port_db(data.port_of_entry, world_ports)
    issues.extend(port_issues)
    total_penalty += port_penalty

    # ── Duty estimate — try DB tariff, fall back to chapter dict ─────────────
    if data.items:
        first_hs = data.items[0].hs_code.strip()
        tariff_row = tariff_map.get(first_hs) or tariff_map.get(first_hs[:2])
        if tariff_row:
            assessable_inr = data.shipment_value_usd * settings.usd_to_inr
            bcd_rate = float(tariff_row.bcd_rate)
            igst_rate = float(tariff_row.igst_rate)
            bcd = assessable_inr * bcd_rate
            sws = bcd * 0.10
            igst = (assessable_inr + bcd + sws) * igst_rate
            total = bcd + sws + igst
            effective_pct = (total / assessable_inr * 100) if assessable_inr > 0 else 0.0
            duty_estimate = DutyEstimate(
                assessable_value_inr=round(assessable_inr, 2),
                bcd_inr=round(bcd, 2),
                sws_inr=round(sws, 2),
                igst_inr=round(igst, 2),
                total_duty_inr=round(total, 2),
                effective_rate_pct=round(effective_pct, 2),
                note=(
                    "Indicative estimate from DB tariff table. "
                    "Verify against the current CBIC Basic Customs Duty schedule and "
                    "applicable exemption notifications before filing."
                ),
            )
        else:
            duty_estimate = _estimate_duty(data)
    else:
        duty_estimate = _estimate_duty(data)

    return _compute_result(issues, suggestions, total_penalty, has_critical, duty_estimate)


# ── In-memory (fallback) evaluation ──────────────────────────────────────────

def _evaluate_in_memory(data: ExtractedShipmentData) -> ComplianceResult:
    """Original rule-based compliance evaluation using hardcoded dicts/sets."""
    issues: list[ComplianceIssue] = []
    total_penalty = 0
    has_critical = False

    for item in data.items:
        origin = item.country_of_origin.lower().strip()
        hs = item.hs_code.strip()
        chapter = hs[:2]
        prefix4 = hs[:4] if len(hs) >= 4 else ""

        # ── Origin checks ─────────────────────────────────────────────────────
        if origin in RESTRICTED_ORIGINS:
            issues.append(ComplianceIssue(
                severity="critical",
                title="Import from Pakistan — trade suspended",
                detail=(
                    f"Direct import of goods from Pakistan is not permitted following India's "
                    f"revocation of MFN status in February 2019. A specific DGFT exemption "
                    f"order is required before this shipment can be filed."
                ),
                regulation="DGFT Public Notice 2/2019; Customs Circular 13/2019",
            ))
            total_penalty += SEVERITY_WEIGHT["critical"]
            has_critical = True

        if origin in UN_SANCTIONED_ORIGINS:
            issues.append(ComplianceIssue(
                severity="critical",
                title="UN-sanctioned country of origin",
                detail=(
                    f"Goods originating in {item.country_of_origin} are subject to mandatory UN "
                    f"Security Council sanctions that India is obliged to implement. "
                    f"Escalate to DGFT and Ministry of External Affairs before proceeding."
                ),
                regulation="UNSC Resolutions; Customs Act 1962 Section 11",
            ))
            total_penalty += SEVERITY_WEIGHT["critical"]
            has_critical = True

        if origin in HIGH_RISK_ORIGINS:
            issues.append(ComplianceIssue(
                severity="high",
                title=f"High-risk origin: {item.country_of_origin}",
                detail=(
                    f"Shipments from {item.country_of_origin} require enhanced due diligence. "
                    f"Verify end-use, confirm no sanctions exposure, and retain detailed "
                    f"supplier documentation for potential customs examination."
                ),
                regulation="Customs Act 1962 Section 11; DGFT FTP 2023",
            ))
            total_penalty += SEVERITY_WEIGHT["high"]

        # ── BIS Compulsory Registration ───────────────────────────────────────
        if prefix4 in BIS_MANDATORY_PREFIXES:
            issues.append(ComplianceIssue(
                severity="high",
                title="BIS Compulsory Registration required",
                detail=(
                    f"HS {item.hs_code} falls under the BIS Compulsory Registration Scheme. "
                    f"The importer must hold a valid BIS licence for this manufacturer/brand "
                    f"before goods can be released from customs. Non-compliant goods are liable "
                    f"to seizure and destruction under the BIS Act 2016."
                ),
                regulation="BIS (Compulsory Registration) Order 2012; BIS Act 2016 Section 17",
            ))
            total_penalty += SEVERITY_WEIGHT["high"]

        # ── FSSAI clearance ───────────────────────────────────────────────────
        if chapter in FSSAI_CHAPTERS:
            issues.append(ComplianceIssue(
                severity="high",
                title="FSSAI import clearance required",
                detail=(
                    f"HS {item.hs_code} is an article of food subject to FSSAI import "
                    f"regulations. The importer must submit a prior intimation on FSSAI's Food "
                    f"Import Management System (FIMS), the foreign manufacturer must be "
                    f"registered with FSSAI, and samples will be tested at a notified port lab "
                    f"before out-of-charge."
                ),
                regulation="Food Safety and Standards (Import) Regulations 2017; FSSAI Circular",
            ))
            total_penalty += SEVERITY_WEIGHT["high"]

        # ── WPC licence ───────────────────────────────────────────────────────
        if prefix4 in WPC_PREFIXES:
            issues.append(ComplianceIssue(
                severity="medium",
                title="WPC licence required for wireless equipment",
                detail=(
                    f"HS {item.hs_code} contains wireless/radio capability and requires a WPC "
                    f"import licence from the Ministry of Communications prior to import. "
                    f"Equipment must also comply with type-approval requirements under the "
                    f"Indian Wireless Telegraphy Act 1933."
                ),
                regulation="Indian Wireless Telegraphy Act 1933; WPC Licensing Guidelines",
            ))
            total_penalty += SEVERITY_WEIGHT["medium"]

        # ── SCOMET / defence items ────────────────────────────────────────────
        if prefix4 in SCOMET_PREFIXES:
            issues.append(ComplianceIssue(
                severity="critical",
                title="SCOMET item — DGFT authorisation required",
                detail=(
                    f"HS {item.hs_code} is classified as a SCOMET (Special Chemicals, "
                    f"Organisms, Materials, Equipment and Technologies) item. Import requires "
                    f"prior authorisation from DGFT, end-use and end-user certificate, and "
                    f"post-import reporting. Unauthorised import is a criminal offence under "
                    f"the Foreign Trade (Development and Regulation) Act 1992."
                ),
                regulation="DGFT SCOMET List, Appendix 3, Schedule 2 ITC-HS; FTDR Act 1992",
            ))
            total_penalty += SEVERITY_WEIGHT["critical"]
            has_critical = True

        # ── Incomplete ITC-HS code ────────────────────────────────────────────
        if len(hs) < 8:
            issues.append(ComplianceIssue(
                severity="medium",
                title="Incomplete ITC-HS code",
                detail=(
                    f"India uses 8-digit ITC-HS codes for Bill of Entry filing. The declared "
                    f"code '{hs}' has only {len(hs)} digit(s). An incomplete code prevents "
                    f"accurate duty assessment and may cause the Bill of Entry to be rejected "
                    f"at ICEGATE."
                ),
                regulation="Customs Tariff Act 1975; CBIC Circular on ITC-HS classification",
            ))
            total_penalty += SEVERITY_WEIGHT["medium"]

    # ── Shipment-level checks ─────────────────────────────────────────────────
    shipment_issues, shipment_penalty = _shipment_level_checks(data)
    issues.extend(shipment_issues)
    total_penalty += shipment_penalty

    duty_estimate = _estimate_duty(data)

    suggestions: list[str] = []
    return _compute_result(issues, suggestions, total_penalty, has_critical, duty_estimate)


# ── Main compliance engine ────────────────────────────────────────────────────

async def evaluate_compliance(data: ExtractedShipmentData) -> ComplianceResult:
    """Evaluate compliance, using DB tables when available, falling back to in-memory rules."""
    if SessionLocal is not None:
        try:
            return await _evaluate_from_db(data)
        except Exception as exc:
            logger.warning(
                "compliance_db_failed reason=%s — falling back to in-memory evaluation", exc
            )
    return _evaluate_in_memory(data)


def build_assistant_summary(data: ExtractedShipmentData, result: ComplianceResult) -> str:
    duty = result.duty_estimate
    duty_line = (
        f" Indicative duty: BCD ₹{duty.bcd_inr:,.0f} + SWS ₹{duty.sws_inr:,.0f} + "
        f"IGST ₹{duty.igst_inr:,.0f} = ₹{duty.total_duty_inr:,.0f} total "
        f"({duty.effective_rate_pct:.1f}% effective rate)."
        if duty else ""
    )
    return (
        f"Shipment for {data.importer} is classified as {result.risk_level.lower()} risk "
        f"with a compliance score of {result.score}/100. "
        f"{len(result.issues)} issue(s) detected.{duty_line} "
        f"Expected outcome: {result.clearance_prediction}."
    )
