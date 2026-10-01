# Go / frontend contract

Binding prefix: `codexio/windows/backend.Service`. Use `@wailsio/runtime` `Call.ByName(prefix + '.' + method, ...args)` behind a single frontend wrapper. All methods return promises; catch errors. Go performs all filesystem/SQLite/network work. Events `codexio:changed` carry `{generation, scope}` and `codexio:notice` carry public status only. Reload only the active page, discard responses issued for previous filters. Do not poll entire UI once per second.

## Public methods (root owns service composition)

- `GetOverview(Query) -> Row`: `{summary, chart, recent, models, comparison, quota, state, generation}`.
- `GetLogs(Query) -> PageResult`: `{rows,total,page,pages,counts}`. Counts keys `requests`, `subagents`, `reviews`, `compactions`, `unassigned`. Page size max100. Modes `user_request`/`model_call`.
- `GetDetails(id: string, page: int) -> Row`: `{request, user, final, attachments, members, composition, page, pages, user_complete, final_complete, availability}`. Full/user/final are source-grounded; keep Markdown safe and support copy.
- `GetTrends(Query) -> Row`: `{summary,chart,models,insights,heatmap,activity,chats,chat_page,chat_pages,chat_total,comparison}`. `insights.stats` contains local activity/mode statistics; heatmap has 365 prepared daily rows, user counts exclude reviews. Chat pages start at five rows. Chart/hover uses prepared metrics and never invokes Go.
- `GetSubscription() -> Row`: quota snapshot plus official reports, cycles/rolling estimate. Quota fields `status`,`updated_at`,`error`,`applicable`,`account`,`plan_type`,`primary`,`secondary`,`credits`,`reports`. Window fields `used_percent`,`remaining_percent`,`window_duration_mins`,`resets_at` (Unix seconds or ISO accepted by formatter). Missing values render `—`, API-key mode explicitly not applicable.
- `GetTrayState() -> Row`: pure quota projection and store status; no official-report request or SQLite aggregation. `Ready() -> void` is called after the main overview has rendered to notify native readiness and acknowledge a verified installed update. `DeferUpdate() -> Row` keeps the existing reminder deferral.
- `GetPrices() -> Row`: `{rows,basis,version,status}`; row base fields `model`,`input`,`cache_read`,`cache_write`,`output`,`service_tier`,`threshold`,`condition`,`source`. USD per million tokens; retain null prices. `SavePrice(model: string,rates: Row) -> Row` modifies only allowed base overrides. `SyncPrices() -> Row` explicitly refreshes the verified price feeds even when automatic synchronization is disabled; conflicts retain the last verified base price.
- `GetReports(period: string) -> Row`: period `day`/`week`/`month`, completed previous natural period. `{period,start,end,date_label,summary,models,time_slices,first,last,previous,style}`. Preserve existing three styles `garden`,`bookmark`,`afternoon`, defaultgarden.
- `GetSettings() -> Row`: public merged preferences + `data_path`,`version`,`mock`,`language`,`mobile`,`upstream`,`update`,`state`. Preserve `theme` light/dark/system, `app_icon`, `codex_roots`, `codex_path`, refreshinterval, reportauto/style, desktop preferences, navigation/column widths, subscriptionprofile.
- `SaveSettings(changes: Row) -> Row`: validated preferences persisted; no direct arbitrary auth/config/file access. Source/model/price changes invalidate relevant backend cache only.
- `Refresh() -> void`, `Rescan() -> void`: asynchronous/coalesced; UI retains existing data with appropriate status.
- `CheckUpdate() -> Row`, `InstallUpdate() -> void`: explicit download/install after user click; progress via scoped events/state. Mock cannot install. Fields `{status,version,notes,progress,error,url}`.
- `ResetCredit(id: string) -> Row`: call only after user selects a concrete card and confirms. Pending unknown result retries same idempotency key, no automatic reset.
- `DesktopAction(action: string) -> void`: `show-main`, `close-main`, `toggle-floating`, `hide-floating`, `quit`, `open-data`, `open-reports`, `show-report`, `restart-client`. Root uses native window/OS APIs and saves final geometry only.
- `OpenURL(url: string) -> void`: explicitly clicked HTTP(S) link in default browser.
- `SaveReportPNG(data: string, period: string) -> Row`: bounded base64PNG, `{path}`; frontend generates the full card, root saves/shares.
- `GetMobile() -> Row`, `MobileAction(action: string, values: Row) -> Row`: `enable`,`disable`,`pair`,`approve`,`deny`,`revoke`,`rename`,`note`,`enroll`,`remove-cloud`,`upload`. Public fields pairing QR/pending/readers/status; no secrets. Pairing up to3readers, ticket5min. Existing read-only protocol.
- `UpstreamAction(enabled: boolean) -> Row`: user-controlled toggle, `{enabled,status,error,restart_required}`; mock cannot modify real route.

## Common field meanings

`Query` JSON uses `mode,period,start,end,model,tier,status,search,source,page,page_size,granularity`. Defaultperiod today; start/end local ISO date or timestamp, upperdate inclusive. `source="local"` restricts verified local contributions (reports/activity). Summary uses `usd,tokens,user_requests,input_tokens,cached_input_tokens,cache_hit_rate,unpriced_calls,unpriced_tokens,cost_complete`.

Ledger/group rows preserve original snake-case: `id,timestamp,record_kind,session_title,prompt_preview,output_preview,model,models,reasoning_effort,service_tier,model_context_window,source_ids,request_status,status_label,cost_usd,pricing_status,unpriced_calls,total_tokens,input_tokens,cached_input_tokens,cache_write_input_tokens,output_tokens,duration_ms,call_count,subagent_count,association_note`. Label `automatic_approval_review` as 自动审批审查 / Automatic approval review. It never increases user_request counts or replaces recent true tasks. `context_compaction` is 上下文压缩 / Context compaction. Unknown price remainsnull.

Request ownership also exposes `root_id,root_turn_id,continuation_of,prompt_source_turn_id,resume_kind,started_at,ended_at,status,message_digest,message_revision,is_approval_review`. Only groups with source-confirmed human input contribute to `user_request` counts; explicit continuations share their original request. `context_message` preserves owned accounting whose human input is still unknown and does not contribute to user counts. Mac `approval_review` and guardian source variants normalize to the existing `automatic_approval_review` wire kind. Message digests exclude source recovery signatures/access timestamps. Desktop and mobile share bounded explicit-member message assembly and indexed source recovery; previews are never promoted to complete bodies.

Local chat ranking totals all verified local meter tokens for a root thread and explicitly known descendants, including independent accounting records. It uses actual thread names, starts with five rows and caps each requested page at 25 rows. Its user-request count remains independent of those meter totals.

Mock smoke bridge is separately bound only in mock+smoke mode as `main.SmokeService.Ready({rendered_tokens: string,...})`; root coordinates exactly the original3checks. Do not create frontend test suite, page traversal or screenshot matrix.
