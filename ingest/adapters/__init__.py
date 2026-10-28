"""
ingest/adapters/ - one module per way of getting data (see each file's header)
=============================================================================

    cma_content     CMA GetContentList API (warnings, violations, funds, registered firms, publications)
    cma_integration CMA GeIntegrationResult API (licensed companies, finfluencers)
    cma_activities  licensed activity -> firms map (many GeIntegrationResult calls)
    html_table      a static <table> on a page (appraisers)
    local_csv       a reference CSV shipped with the app (FCSC CPI)
    remote_table    ANY table on the web (CSV / Excel / JSON / HTML), used by sources the onboarding agent adds

ADAPTERS maps the registry's `kind` to the module whose fetch(spec, ctx) handles it.
"""
from . import cma_activities, cma_content, cma_integration, html_table, local_csv, remote_table

ADAPTERS = {
    "cma_content_list": cma_content,
    "cma_integration": cma_integration,
    "cma_activity_map": cma_activities,
    "html_table": html_table,
    "local_csv": local_csv,
    "remote_table": remote_table,
}
