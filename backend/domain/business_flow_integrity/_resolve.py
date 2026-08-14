"""Typed name-resolver joining BD binding tokens to manifest rel_paths."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Literal

ResolutionStatus = Literal["RESOLVED", "UNRESOLVED_EXTERNAL", "AMBIGUOUS"]


@dataclass(frozen=True)
class ResolveResult:
    status: ResolutionStatus
    rel_path: str | None = None
    candidate_paths: list[str] = field(default_factory=list)


def _extension_matches(rel_path: str, asset_type: str | None) -> bool:
    if not asset_type:
        return True
    ext = os.path.splitext(rel_path)[1].lower()
    atype = asset_type.strip().upper()

    if atype.startswith("."):
        return ext == atype.lower()

    if "CLIST" in atype or atype in ("PROCEDURE",):
        return ext == ".clist"
    if "JCL" in atype or atype in ("JOB",):
        return ext in (".jcl", ".prc")
    if "COBOL" in atype or "CBL" in atype:
        return ext in (".cbl", ".cob")
    # BD prose uses "program"/"step" generically for any routed executable (COBOL, CLIST,
    # PFD/menu, JCL, ISPF) — not COBOL-exclusively. Accept the whole executable family so
    # generic-typed references (e.g. PHNIXLOT.clist, HSBMENU5.pfd) resolve instead of being
    # dropped to UNKNOWN. Genuine multi-candidate collisions degrade to AMBIGUOUS, which the
    # _align candidate ranker then disambiguates by real node_kind.
    if atype in ("PROGRAM", "STEP"):
        return ext in (".cbl", ".cob", ".clist", ".pfd", ".ipf", ".jcl", ".prc")
    if "PANEL" in atype or "IPF" in atype or "PFD" in atype or atype in ("SCREEN",):
        return ext in (".ipf", ".pfd")

    return True



def resolve_asset(
    token: str,
    asset_type: str | None,
    manifest_paths: list[str] | list[dict[str, Any]],
) -> ResolveResult:
    """Join a BD binding token to a manifest_files.rel_path, strictly typed.

    Resolution order:
    1. Exact normalized rel_path match
    2. Exact case-insensitive basename match
    3. Unique (stem, asset_type) match
    """
    if not token or not token.strip():
        return ResolveResult("UNRESOLVED_EXTERNAL")

    clean_token = token.strip().replace("\\", "/")
    
    # Extract string paths
    all_paths: list[str] = []
    for item in manifest_paths:
        if isinstance(item, dict):
            p = item.get("rel_path")
            if p:
                all_paths.append(p)
        elif isinstance(item, str):
            all_paths.append(item)

    if not all_paths:
        return ResolveResult("UNRESOLVED_EXTERNAL")

    # Step 1: Exact normalized rel_path match
    for p in all_paths:
        if p.replace("\\", "/") == clean_token:
            return ResolveResult("RESOLVED", rel_path=p)

    # Step 2: Exact case-insensitive basename match
    token_base = clean_token.rsplit("/", 1)[-1].lower()
    base_matches = [p for p in all_paths if p.rsplit("/", 1)[-1].lower() == token_base]
    if len(base_matches) == 1:
        return ResolveResult("RESOLVED", rel_path=base_matches[0])
    elif len(base_matches) > 1:
        typed_base = [p for p in base_matches if _extension_matches(p, asset_type)]
        if len(typed_base) == 1:
            return ResolveResult("RESOLVED", rel_path=typed_base[0])
        return ResolveResult("AMBIGUOUS", candidate_paths=base_matches)

    # Step 3: Unique (stem, asset_type) match
    token_stem = clean_token.rsplit("/", 1)[-1].split(".")[0].upper()
    stem_matches = [
        p for p in all_paths if p.rsplit("/", 1)[-1].split(".")[0].upper() == token_stem
    ]

    if asset_type:
        typed_stem_matches = [p for p in stem_matches if _extension_matches(p, asset_type)]
    else:
        typed_stem_matches = stem_matches

    if len(typed_stem_matches) == 1:
        return ResolveResult("RESOLVED", rel_path=typed_stem_matches[0])
    elif len(typed_stem_matches) > 1:
        return ResolveResult("AMBIGUOUS", candidate_paths=typed_stem_matches)

    return ResolveResult("UNRESOLVED_EXTERNAL")
