# Claude workers interface

Inherit Claude Code’s native Box, Text, Button and Input controls and theme. Operate mode: scanning several workers and resolving blocked work come first.

## Approved layout

The landing view is Overview. Each worker row shows task, native CLI, lifecycle status, last-update recency, origin Claude session and latest activity. Keep three collapsible sections in fixed order:

1. **Idle**: Needs input and Failed.
2. **Working**: Starting, Working and Stopping.
3. **Finish**: Done, Ready (empty session with no turn), Interrupted, Closed and Offline.

The native `idle` protocol state means an empty Ready session; it is not the display section Idle. Preserve actual row statuses even when grouped.

Filters are mutually exclusive: Active session (default, every record of the current host), Active (starting/running/waiting/stopping across accessible parents), All (all accessible records). Group counts follow the filter. Failed remains in Idle under All/Active session and is excluded from Active.

Delegated workers show `From Claude: <parent title>`; direct sessions show `Direct session`. Details show the parent short ID for delegated/unknown origin. Unknown origin, including older bridges, is labeled Host Claude rather than claiming delegation. Resolve the actual transcript custom title, then first user prompt, then a stable short-ID fallback.

## Responsive behavior

At 96 columns and above, retain the overview beside the selected worker’s Activity/Chat or New session form. Narrower panes show the overview first, then selected details or creation as a secondary view with Back to overview. Finish starts collapsed on compact panes. Preserve each worker’s message draft independently. A pane uses the host’s scrolling and theme, not a separate web dashboard.

## Controls and state

New session chooses CLI, folder, optional model and optional first message. Empty creation sends no model turn. Show real protocol tool events, bounded history, pending approval Allow once/Deny, messages and Interrupt/Close controls on the selected reachable worker. Offline is last-known information; disable controls until its parent is reachable. Show empty filters, no sessions, loading, errors and Reconnect explicitly.

## Compatibility

Keep native sessions alive when reloading onto a 0.2 bridge. It supports the redesigned current-session pane; cross-session discovery requires a new Claude session using the 0.3 bridge. Do not automatically restart a bridge with live workers. No unsupported global sidebar or toolbar injection.

## Desktop polish — 0.3.1

Header uses full-width space-between alignment. Only the selected filter uses the native filled control. A single centered empty state replaces three empty section controls; section headings return when records exist. Connection status sits after a flexible spacer and Reconnect is shown only while disconnected. Worker rows reserve a stable status column and remove the extra Selected word. Native authored chevrons replace Show/Hide text on section headings. Do not add redundant keyboard hints to the primary New session button.

## Task detail and management — 0.3.2

Activity defaults to the latest five tool calls. Show short titles and human lifecycle words; put long commands and decoded ACP text inside per-tool disclosures. Older tools load in batches of five. Do not repeat the same tool lifecycle through the raw event list. Non-tool events and raw error details live under Diagnostics. Task info is secondary; Activity/Chat are native selected tabs. Chat initially shows the latest four messages, with earlier history available explicitly.

The native message Input already supplies a Send action; do not add a second Send button. Keep Interrupt and Close in one secondary action row. Short task identity, provider, actual state and parent attribution precede content.

Actions contains Archive/Unarchive, Delete with a concrete stop-and-remove confirmation, Session info, and Retry failed. Failed overview rows also expose Retry. Archive is a presentation flag and does not stop a process. Normal views exclude archived records; Archived shows them across accessible parents in the current host workspace. Archive/deletion metadata uses per-worker keys in the native shared plugin store, surviving UI reload and avoiding a shared map overwrite across sessions.

Retry creates a fresh native session with the same original task, worker kind, folder and model, selects the new attempt and archives the old failed record. Prior native context is not resumed. Delete stops a reachable worker and removes its monitor record; native CLI log files are not deleted. Offline deletion hides its last-seen monitor record and explicitly cannot stop an unreachable process. Existing daemons are never restarted to enable management controls.

## Full native composer — 0.4.0

Preserve the original Claude composer and its layout. AbovePrompt adds a compact Harness select (Claude, Codex, Qwen Code, OpenCode), then a Worker model select and target label only when a worker harness is selected. Claude retains its native model picker. The worker model is chosen in the Workers band; the Claude model picker is not repurposed. Worker choices come from local Codex cache, Qwen configured providers, or OpenCode CLI catalog, plus CLI default and Custom model ID. Do not hardcode model availability.

The native composer starts on Claude after reload. Only human-origin composer text is routed to a worker. Slash commands, plugin wake-ups, peer messages, schedules and notifications remain on Claude. First text creates a direct native session; subsequent text reuses that target. Changing harness/model or choosing New conversation explicitly clears the target and starts fresh on next send. Existing sessions stay alive. A selected pane worker has Use full composer to choose that exact conversation. Replies stay in Workers Chat; do not impersonate them as Claude messages.

Worker routing is text-only because Mods attachments expose type/name metadata, not attachment bytes. Refuse such submissions with an explicit reason; never silently drop attachments or route them to another model. Claude-mode attachments retain native behavior. Failure or uncertainty keeps text under Restore draft, with no automatic resend or fallback to Claude. The prompt-routing gate has a fail-closed catch handler.

Back to overview/Close details, Actions, and Close session use visible native button backgrounds rather than plain text styling.

### Compact composer toolbar — 0.4.1

Keep Workers, Harness and Model in one responsive row. Zero counts and default routing helper prose are absent. A selected existing conversation shows a short Task label so the recipient remains visible before sending. New conversation and Refresh models live behind Options; session explanation is only visible when that menu is expanded. Custom model input, actual delivery progress, pending approval counts and errors may add content when relevant. Preserve native editor spacing and the model/harness choices.

Options spacing (0.4.2): separate the toolbar, session explanation and action row with native top padding and row gap; keep bottom padding before the composer. This refines spacing only and preserves routing and actions.

## Transcript progress card — 0.5.0
A compact, theme-native outlined card uses one summary action with a chevron and an optional worker subtitle. Scope is current parent and unarchived workers. Render hooks target only this plugin's start tool and workers command. Keep native failed/interrupted rows and mixed-group background controls. Display last-known status on disconnect. Opening an aggregate resets the filter to Active session; an individual card opens its own worker detail.


## Claude-only composer — 0.5.0

The main composer is Claude-only. AbovePrompt draws one slim row: the Workers button and an "N active / N need input" label. Harness and model dropdowns live on the pane's New session screen (Select keys `launch-harness`, `launch-model-<kind>`); the catalog loads on demand. The per-worker Chat tab owns messaging: a bordered, 3-row Input, with Interrupt (left) and Close session (right) on a footer row. The 1 s bridge poll redraws only when the visible state changed (signature compare) so an open dropdown keeps its scroll position. Sections 0.4.0-0.4.2 above are superseded.

## Worker pane composer — 0.5.2
Match the reference's reading order: one message input first, one secondary control row below it. Remove the enclosing frame around the already framed native Input and use a compact two-row multiline field so the Desktop renderer fills the available width. The footer places Start empty session and Options on the left, harness/model selects on the right; it wraps into two groups in narrow panes. Refresh models lives under Options and custom model entry only appears when selected. Opening Options/refreshing must preserve the draft and chosen model. Existing worker chat uses the same single-frame message field. The host Input owns its exact radius, background and submit button rendering; Mods offers no CSS override for it. Do not draw inactive attachment, microphone or permission controls.
