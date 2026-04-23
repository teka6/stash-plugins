/**
 * Performer Hub — UI overlay (vanilla JS, no external libraries).
 *
 * Injects scene-count badges next to StashDB ID pills on performer and studio pages.
 * Badge format: "<local_count> / <stashbox_count>".
 *
 * Zero dependencies — uses fetch('/graphql'), MutationObserver, and history-API monkey-patch.
 */
(function () {
    'use strict';

    const PLUGIN_ID = 'performer_hub';
    const PLUGIN_DISPLAY_NAME = 'Performer Hub';
    const LOG_PREFIX = `[Plugin / ${PLUGIN_DISPLAY_NAME}]`;

    const STYLE = `
        .stash-id-pill span.phub-scene-count {
            display: inline-block;
            margin-left: .25rem;
            padding: 0 .35rem;
            border-radius: .25rem;
            background-color: #394b59;
            font-size: 0.85em;
            font-weight: 500;
        }
    `;

    // Inject stylesheet once
    const styleEl = document.createElement('style');
    styleEl.textContent = STYLE;
    document.head.appendChild(styleEl);

    // ────────────────────────────────────────────────────────
    // Stash GraphQL helpers (same-origin fetch, uses session cookie)
    // ────────────────────────────────────────────────────────

    async function gql(query, variables) {
        const resp = await fetch('/graphql', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, variables: variables || {} }),
        });
        if (!resp.ok) throw new Error(`GraphQL HTTP ${resp.status}`);
        const json = await resp.json();
        if (json.errors && json.errors.length) {
            throw new Error(`GraphQL errors: ${JSON.stringify(json.errors)}`);
        }
        return json.data || {};
    }

    let _pluginSettingsCache = null;
    async function getPluginSettings() {
        if (_pluginSettingsCache) return _pluginSettingsCache;
        const data = await gql('{ configuration { plugins } }');
        const plugins = (data.configuration && data.configuration.plugins) || {};
        _pluginSettingsCache = plugins[PLUGIN_ID] || {};
        return _pluginSettingsCache;
    }

    let _stashBoxesCache = null;
    async function getStashBoxes() {
        if (_stashBoxesCache) return _stashBoxesCache;
        const data = await gql(`{
            configuration { general { stashBoxes { endpoint name api_key } } }
        }`);
        _stashBoxesCache = (((data.configuration || {}).general || {}).stashBoxes) || [];
        return _stashBoxesCache;
    }

    async function findPerformerById(id) {
        const data = await gql(
            'query($id: ID!){ findPerformer(id: $id){ id stash_ids { endpoint stash_id } } }',
            { id }
        );
        return data.findPerformer;
    }

    async function findStudioById(id) {
        const data = await gql(
            'query($id: ID!){ findStudio(id: $id){ id stash_ids { endpoint stash_id } } }',
            { id }
        );
        return data.findStudio;
    }

    async function runPluginTask(taskName, argMap) {
        // argMap: { key: value } — value must be a string (we only pass strings)
        const args = Object.entries(argMap).map(([key, value]) => ({
            key,
            value: { str: String(value) },
        }));
        const mutation = `mutation($plugin_id: ID!, $task_name: String, $args_map: [PluginArgInput!]) {
            runPluginTask(plugin_id: $plugin_id, task_name: $task_name, args_map: $args_map)
        }`;
        return gql(mutation, { plugin_id: PLUGIN_ID, task_name: taskName, args_map: args });
    }

    /**
     * Poll Stash logs for a line containing `marker`. Returns everything after the marker.
     * @param {string} marker e.g. "[Plugin / Performer Hub] abc-123: "
     * @param {number} timeoutMs
     * @returns {Promise<string|null>} the suffix, or null on timeout
     */
    async function pollLogsFor(marker, timeoutMs = 20000) {
        const deadline = Date.now() + timeoutMs;
        const startTime = Date.now();
        while (Date.now() < deadline) {
            try {
                const data = await gql('{ logs { time level message } }');
                const entries = data.logs || [];
                for (const entry of entries) {
                    const msg = entry.message || '';
                    const idx = msg.indexOf(marker);
                    if (idx !== -1) {
                        // Only accept entries newer than poll start (best-effort filter against stale logs)
                        const entryTime = Date.parse(entry.time);
                        if (!isNaN(entryTime) && entryTime < startTime - 5000) continue;
                        return msg.substring(idx + marker.length).trim();
                    }
                }
            } catch (_) {
                // transient errors are fine; keep polling
            }
            await sleep(500);
        }
        return null;
    }

    function sleep(ms) {
        return new Promise(r => setTimeout(r, ms));
    }

    // ────────────────────────────────────────────────────────
    // DOM helpers (replacing stashUserscriptLibrary7dJx1qP)
    // ────────────────────────────────────────────────────────

    function waitForSelector(selector, timeoutMs = 5000) {
        return new Promise(resolve => {
            const existing = document.querySelector(selector);
            if (existing) return resolve(existing);

            const obs = new MutationObserver(() => {
                const el = document.querySelector(selector);
                if (el) {
                    obs.disconnect();
                    resolve(el);
                }
            });
            obs.observe(document.body, { childList: true, subtree: true });
            setTimeout(() => {
                obs.disconnect();
                resolve(document.querySelector(selector));
            }, timeoutMs);
        });
    }

    function findStashIdPillLinks() {
        // Each stash-id on a performer/studio page renders as `<span class="stash-id-pill"><a>UUID</a>…</span>`
        return Array.from(document.querySelectorAll('span.stash-id-pill a'));
    }

    function insertBadgeAfter(anchor, text) {
        const badge = document.createElement('span');
        badge.className = 'phub-scene-count';
        badge.textContent = text;
        anchor.parentElement.insertBefore(badge, anchor.nextSibling);
        return badge;
    }

    // ────────────────────────────────────────────────────────
    // Page handlers
    // ────────────────────────────────────────────────────────

    function routeMatch(pattern) {
        // pattern: e.g. /^\/performers\/(\d+)$/ — returns [1] on match, null otherwise
        const m = window.location.pathname.match(pattern);
        return m ? m[1] : null;
    }

    async function handlePerformerPage() {
        const performerId = routeMatch(/^\/performers\/(\d+)/);
        if (!performerId) return;

        const settings = await getPluginSettings();
        if (settings.scene_count_performers !== true) return; // default OFF — user opts in via Settings

        const pill = await waitForSelector('span.stash-id-pill a', 5000);
        if (!pill) return;

        const performer = await findPerformerById(performerId);
        if (!performer || !performer.stash_ids || !performer.stash_ids.length) return;

        const stashBoxes = await getStashBoxes();

        for (const { endpoint, stash_id } of performer.stash_ids) {
            const link = Array.from(document.querySelectorAll('span.stash-id-pill a'))
                .find(a => a.textContent.trim() === stash_id);
            if (!link || link.parentElement.querySelector('.phub-scene-count')) continue;

            const apiKey = (stashBoxes.find(b => b.endpoint === endpoint) || {}).api_key;
            if (!apiKey) continue;

            const badge = insertBadgeAfter(link, '…');
            try {
                await runPluginTask('UI: Stashbox Performer Scene Count', {
                    endpoint, api_key: apiKey, stash_id,
                });
                const result = await pollLogsFor(`${LOG_PREFIX} ${stash_id}: `);
                badge.textContent = result || 'err';
            } catch (e) {
                badge.textContent = 'err';
                console.warn('[performer_hub] performer scene-count failed:', e);
            }
        }
    }

    async function handleStudioPage() {
        const studioId = routeMatch(/^\/studios\/(\d+)/);
        if (!studioId) return;

        const settings = await getPluginSettings();
        if (settings.scene_count_studios !== true) return;

        const pill = await waitForSelector('span.stash-id-pill a', 5000);
        if (!pill) return;

        const studio = await findStudioById(studioId);
        if (!studio || !studio.stash_ids || !studio.stash_ids.length) return;

        const stashBoxes = await getStashBoxes();

        for (const { endpoint, stash_id } of studio.stash_ids) {
            const link = Array.from(document.querySelectorAll('span.stash-id-pill a'))
                .find(a => a.textContent.trim() === stash_id);
            if (!link || link.parentElement.querySelector('.phub-scene-count')) continue;

            const apiKey = (stashBoxes.find(b => b.endpoint === endpoint) || {}).api_key;
            if (!apiKey) continue;

            const badge = insertBadgeAfter(link, '…');
            try {
                await runPluginTask('UI: Stashbox Studio Scene Count', {
                    endpoint, api_key: apiKey, stash_id,
                });
                const result = await pollLogsFor(`${LOG_PREFIX} ${stash_id}: `);
                badge.textContent = result || 'err';
            } catch (e) {
                badge.textContent = 'err';
                console.warn('[performer_hub] studio scene-count failed:', e);
            }
        }
    }

    // ────────────────────────────────────────────────────────
    // Route detection — Stash is a SPA, so watch history.pushState + popstate
    // ────────────────────────────────────────────────────────

    function onRouteChange(callback) {
        let lastPath = window.location.pathname;
        const fire = () => {
            if (window.location.pathname !== lastPath) {
                lastPath = window.location.pathname;
                callback();
            }
        };

        const origPush = history.pushState;
        history.pushState = function () {
            origPush.apply(this, arguments);
            setTimeout(fire, 50);
        };
        const origReplace = history.replaceState;
        history.replaceState = function () {
            origReplace.apply(this, arguments);
            setTimeout(fire, 50);
        };
        window.addEventListener('popstate', () => setTimeout(fire, 50));
    }

    async function onCurrentRoute() {
        const path = window.location.pathname;
        if (/^\/performers\/\d+/.test(path)) {
            await handlePerformerPage();
        } else if (/^\/studios\/\d+/.test(path)) {
            await handleStudioPage();
        }
    }

    // Initial load + SPA route changes
    onRouteChange(onCurrentRoute);
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', onCurrentRoute);
    } else {
        onCurrentRoute();
    }
})();
