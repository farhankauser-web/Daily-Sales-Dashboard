from django.urls import path
from . import views
from . import views_inventory as vi
from . import views_sti
from . import views_marketing

app_name = 'dashboard'

urlpatterns = [
    path('',                     views.index,              name='index'),
    path('historical/',          views.historical,         name='historical'),
    path('cogs/',                views.cogs,               name='cogs'),
    path('cogs/fba-template/',   views.fba_rates_template_xlsx, name='fba_template'),
    path('cogs/missing/',        views.cogs_missing_csv,   name='cogs_missing_csv'),

    # FBA Fee Drift (settlement-actual vs uploaded)
    path('fba-drift/',                  views.fba_fee_drift,            name='fba_fee_drift'),
    path('api/fba-drift/',              views.api_fba_fee_drift,        name='api_fba_fee_drift'),
    path('fba-drift/corrected-xlsx/',   views.fba_drift_corrected_xlsx, name='fba_drift_corrected_xlsx'),

    # Management P&L (settlement + COGS + PPC + manual overhead)
    path('pnl-statement/',              views.pnl_statement,            name='pnl_statement'),
    path('api/pnl-statement/',          views.api_pnl_statement,        name='api_pnl_statement'),
    path('api/pnl-statement/entry/',    views.save_pnl_entry,           name='save_pnl_entry'),
    path('api/pnl-statement/fx/',       views.save_fx_rate,             name='save_fx_rate'),
    path('api/pnl-statement/import/',   views.import_pnl_xlsx,          name='import_pnl_xlsx'),
    path('api/pnl-statement/import-unified/', views.import_unified_txn,  name='import_unified_txn'),
    path('api/cogs/recalc/',            views.api_recalc_cogs,          name='api_recalc_cogs'),
    path('api/pnl-statement/sync/',     views.api_sync_pnl_month,       name='api_sync_pnl_month'),

    # Cash Flow & Balance (Amazon payouts)
    path('cash-flow/',                  views.cash_flow,                name='cash_flow'),
    path('api/cash-flow/',              views.api_cash_flow,            name='api_cash_flow'),

    # MCF Orders (multi-channel fulfillment tracking)
    path('mcf/',                        views.mcf_orders,               name='mcf_orders'),
    path('api/mcf/',                    views.api_mcf_orders,           name='api_mcf_orders'),
    path('api/mcf/sync/',               views.api_mcf_sync,             name='api_mcf_sync'),
    path('mcf/export/',                 views.mcf_export_csv,           name='mcf_export_csv'),
    path('targets/',             views.targets,            name='targets'),
    path('targets/template/',    views.targets_template_xlsx, name='targets_template'),
    path('catalog/',             views.catalog,            name='catalog'),
    path('catalog/new/',         views.product_form,       name='product_create'),
    path('catalog/<int:pk>/',    views.product_form,       name='product_edit'),
    path('summary/',             views.executive_summary,  name='summary'),
    path('summary/stream/',      views.summary_stream,     name='summary_stream'),
    path('export/',              views.export_csv,         name='export'),
    path('product-line/',        views.product_line_analysis, name='product_line'),

    # Hourly Patterns
    path('hourly/',              views.hourly_patterns,      name='hourly_patterns'),
    path('api/hourly-patterns/', views.api_hourly_patterns,  name='api_hourly_patterns'),
    path('api/hourly-patterns/sku/',    views.api_hourly_patterns_sku,    name='api_hourly_patterns_sku'),
    path('api/hourly-patterns/upload/', views.upload_manual_hourly,       name='upload_manual_hourly'),
    path('api/hourly-patterns/uploads/',views.list_manual_hourly_uploads, name='list_manual_hourly_uploads'),

    # Phase 1 — Campaign Intelligence
    path('campaigns/',                     views.campaigns_list,        name='campaigns_list'),
    path('campaigns/<str:campaign_id>/',   views.campaign_detail,       name='campaign_detail'),
    path('api/campaigns/',                 views.api_campaigns_list,    name='api_campaigns_list'),
    path('api/campaigns/<str:campaign_id>/',          views.api_campaign_detail,    name='api_campaign_detail'),
    path('api/campaigns/<str:campaign_id>/skus/',     views.api_campaign_top_skus,  name='api_campaign_top_skus'),
    path('api/campaigns/<str:campaign_id>/daily/',    views.api_campaign_daily,     name='api_campaign_daily'),
    path('api/campaigns/<str:campaign_id>/targeting/',views.api_campaign_targeting, name='api_campaign_targeting'),
    path('api/campaigns/<str:campaign_id>/hourly/',   views.api_campaign_hourly,    name='api_campaign_hourly'),

    # Marketing Optimizer (search-term actions + budget pacing) — read-only
    path('marketing-optimizer/',           views_marketing.marketing_optimizer, name='marketing_optimizer'),
    path('api/mkt-search-terms/',          views_marketing.api_mkt_search_terms, name='api_mkt_search_terms'),
    path('api/budget-pacing/',             views_marketing.api_budget_pacing,   name='api_budget_pacing'),
    path('api/mkt-export/',                views_marketing.mkt_export,          name='mkt_export'),

    # Search Term Intelligence
    path('search-terms/',                  views.search_terms,            name='search_terms'),
    path('api/search-terms/',              views.api_search_terms,        name='api_search_terms'),
    path('api/search-terms/detail/',       views.api_search_term_detail,  name='api_search_term_detail'),

    # Phase 5 — Search Intelligence Center
    path('search-intelligence/',            views_sti.search_intelligence,   name='search_intelligence'),
    path('search-intelligence/generate/',   views_sti.sti_generate,          name='sti_generate'),
    path('search-intelligence/outcomes/',   views_sti.sti_outcomes,          name='sti_outcomes'),
    path('search-intelligence/groups/',     views_sti.sti_groups,            name='sti_groups'),
    path('search-intelligence/groups/new/', views_sti.sti_group_form,        name='sti_group_create'),
    path('search-intelligence/groups/<int:pk>/', views_sti.sti_group_form,   name='sti_group_edit'),
    path('api/sti/run/<int:pk>/narrate/',   views_sti.sti_narrate,           name='sti_narrate'),
    path('api/sti/opportunity/<int:pk>/status/',
                                            views_sti.sti_opportunity_status, name='sti_opportunity_status'),

    # Placement Analytics
    path('placements/',                    views.placements,              name='placements'),
    path('api/placements/',                views.api_placements,          name='api_placements'),

    # Leaderboards
    path('leaderboards/',                  views.leaderboards,            name='leaderboards'),
    path('api/leaderboards/',              views.api_leaderboards,        name='api_leaderboards'),

    # Phase 2 — Executive P&L Center
    path('pnl/',                       views.pnl_daily,            name='pnl_daily'),
    path('api/pnl/',                   views.api_pnl_daily,        name='api_pnl_daily'),
    path('pnl/skus/',                  views.pnl_skus,             name='pnl_skus'),
    path('api/pnl/skus/',              views.api_pnl_skus,         name='api_pnl_skus'),
    path('pnl/breakdown/',             views.pnl_breakdown,        name='pnl_breakdown'),
    path('api/pnl/breakdown/',         views.api_pnl_breakdown,    name='api_pnl_breakdown'),
    path('morning-report/',            views.morning_report,       name='morning_report'),
    path('api/morning-report/',        views.api_morning_report,   name='api_morning_report'),
    path('api/morning-report/ai-commentary/',
                                       views.api_morning_ai_commentary,
                                       name='api_morning_ai_commentary'),

    # Phase 3 — Brand Analytics
    path('ba/queries/',                views.ba_queries,           name='ba_queries'),
    path('api/ba/queries/',            views.api_ba_queries,       name='api_ba_queries'),
    path('ba/baskets/',                views.ba_baskets,           name='ba_baskets'),
    path('api/ba/baskets/',            views.api_ba_baskets,       name='api_ba_baskets'),
    path('ba/market-share/',           views.ba_market_share,      name='ba_market_share'),
    path('api/ba/market-share/',       views.api_ba_market_share,  name='api_ba_market_share'),
    path('ba/share-trend/',            views.ba_share_trend,       name='ba_share_trend'),
    path('api/ba/share-trend/',        views.api_ba_share_trend,   name='api_ba_share_trend'),

    # Phase 4 — AI Insights
    path('ai/recommendations/',                views.ai_recommendations,        name='ai_recommendations'),
    path('api/ai/recommendations/',            views.api_ai_recommendations,    name='api_ai_recommendations'),
    path('api/ai/recommendations/<int:pk>/status/',
                                               views.api_ai_rec_status,         name='api_ai_rec_status'),
    path('api/ai/recommendations/regenerate/', views.api_ai_recs_regenerate,    name='api_ai_recs_regenerate'),

    # Inventory
    path('inventory/',               vi.inventory,          name='inventory'),
    path('inventory/<int:pk>/history/', vi.inventory_history, name='inventory_history'),
    path('inventory/update/',        vi.inventory_update,   name='inventory_update'),

    # PPC Analytics
    path('ppc/',                     vi.ppc_analytics,      name='ppc'),

    # Alerts
    path('alerts/',                  vi.alerts,             name='alerts'),
    path('alerts/<int:pk>/resolve/', vi.alert_resolve,      name='alert_resolve'),
    path('alerts/api/',              vi.alerts_api,         name='alerts_api'),
]
