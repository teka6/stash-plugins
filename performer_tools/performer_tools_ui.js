(function() {
    'use strict';

    const {
        stash,
        Stash,
        waitForElementId,
        waitForElementClass,
        waitForElementByXpath,
        getElementByXpath,
        insertAfter,
        getClosestAncestor,
        createElementFromHTML,
        updateTextInput,
    } = window.stash7dJx1qP;

    const PLUGIN_ID = 'performer_tools';
    const PLUGIN_NAME = 'Performer Tools';

    document.body.appendChild(document.createElement('style')).textContent = `
    .stash-id-pill span.stashbox-scene-count { border-radius: .25rem; background-color: #394b59; }
    .pt-dry-run-inline {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        margin-left: 10px;
        vertical-align: middle;
    }
    .pt-dry-run-inline label {
        margin: 0;
        cursor: pointer;
        user-select: none;
        font-size: 13px;
        color: #a7b6c2;
    }
    .pt-switch {
        position: relative;
        width: 32px;
        height: 18px;
        background: #394b59;
        border-radius: 9px;
        cursor: pointer;
        transition: background 0.2s;
        flex-shrink: 0;
    }
    .pt-switch.active {
        background: #137cbd;
    }
    .pt-switch::after {
        content: '';
        position: absolute;
        top: 2px;
        left: 2px;
        width: 14px;
        height: 14px;
        background: white;
        border-radius: 50%;
        transition: transform 0.2s;
    }
    .pt-switch.active::after {
        transform: translateX(14px);
    }
    `;

    const STASHDB_ENDPOINT = 'https://stashdb.org/graphql';

    let settings = null;
    async function getSettings() {
        if (settings === null) {
            settings = await stash.getPluginConfig(PLUGIN_ID) || {};
        }
        return settings;
    }

    async function isSceneCountEnabled(page) {
        const s = await getSettings();
        let bUpdate = false;
        if (s?.sceneCountPerformers === undefined && page === 'performers') {
            s.sceneCountPerformers = true;
            bUpdate = true;
        }
        if (s?.sceneCountStudios === undefined && page === 'studios') {
            s.sceneCountStudios = true;
            bUpdate = true;
        }
        if (bUpdate) {
            await stash.updatePluginConfig(PLUGIN_ID, s);
        }
        if (page === 'performers') return s?.sceneCountPerformers !== false;
        if (page === 'studios') return s?.sceneCountStudios !== false;
        return true;
    }

    // ── Dry Run Toggles on Tasks Page ──────────────────────

    const DRY_RUN_TASKS = ['Sync Favorites', 'Sync StashDB Only', 'Enrich Performers', 'Enrich All Favorites'];
    const allSwitches = [];

    function syncAllSwitches(active) {
        for (const sw of allSwitches) {
            sw.classList.toggle('active', active);
        }
    }

    async function injectDryRunToggles() {
        // Find plugin section
        const settingGroups = document.querySelectorAll('#tasks-panel .setting-group, .setting-group');
        let pluginGroup = null;
        for (const group of settingGroups) {
            const header = group.querySelector('.setting');
            if (header && header.innerText.trim() === PLUGIN_NAME) {
                pluginGroup = group;
                break;
            }
        }
        if (!pluginGroup) return false;
        if (pluginGroup.querySelector('.pt-dry-run-inline')) return true;

        const s = await getSettings();
        const isDryRun = s.dryRun !== false;

        // Find each task row and inject toggle next to the button for supported tasks
        const taskSettings = pluginGroup.querySelectorAll('.collapsible-section .setting');
        for (const taskEl of taskSettings) {
            const h3 = taskEl.querySelector('h3');
            if (!h3) continue;
            const taskName = h3.innerText.trim();
            if (!DRY_RUN_TASKS.includes(taskName)) continue;

            const btn = taskEl.querySelector('button');
            if (!btn) continue;

            const wrapper = document.createElement('span');
            wrapper.className = 'pt-dry-run-inline';
            wrapper.innerHTML = `
                <div class="pt-switch ${isDryRun ? 'active' : ''}"></div>
                <label>Dry Run</label>
            `;

            const switchEl = wrapper.querySelector('.pt-switch');
            allSwitches.push(switchEl);

            switchEl.addEventListener('click', async (e) => {
                e.stopPropagation();
                const current = await getSettings();
                const newVal = current.dryRun === false;
                current.dryRun = newVal;
                settings = current;
                await stash.updatePluginConfig(PLUGIN_ID, current);
                syncAllSwitches(newVal);
            });

            btn.parentElement.insertBefore(wrapper, btn.nextSibling);
        }
        return pluginGroup.querySelector('.pt-dry-run-inline') !== null;
    }

    stash.addEventListener('page:settings:tasks', () => {
        let attempts = 0;
        const interval = setInterval(() => {
            injectDryRunToggles().then(done => {
                attempts++;
                if (attempts > 30 || done) clearInterval(interval);
            });
        }, 200);
    });

    // ── Scene Count Tasks ────────────────────────────────────

    async function runGetStashboxPerformerSceneCountTask(endpoint, api_key, stashId) {
        return stash.runPluginTask(PLUGIN_ID, "Get Stashbox Performer Scene Count", [{"key":"endpoint", "value":{"str": endpoint}}, {"key":"api_key", "value":{"str": api_key}}, {"key":"stash_id", "value":{"str": stashId}}]);
    }

    async function runGetStashboxStudioSceneCountTask(endpoint, api_key, stashId) {
        return stash.runPluginTask(PLUGIN_ID, "Get Stashbox Studio Scene Count", [{"key":"endpoint", "value":{"str": endpoint}}, {"key":"api_key", "value":{"str": api_key}}, {"key":"stash_id", "value":{"str": stashId}}]);
    }

    async function getPerformer() {
        const performerId = window.location.pathname.split('/').find((o, i, arr) => i > 1 && arr[i - 1] == 'performers');
        const reqData = {
            "operationName": "FindPerformer",
            "variables": { "id": performerId },
            "query": `query FindPerformer($id: ID!) {
                findPerformer(id: $id) {
                  id
                  stash_ids { endpoint stash_id }
                }
              }`
        };
        const result = await stash.callGQL(reqData);
        return result?.data?.findPerformer;
    }

    async function getStudio() {
        const studioId = window.location.pathname.split('/').find((o, i, arr) => i > 1 && arr[i - 1] == 'studios');
        const reqData = {
            "operationName": "FindStudio",
            "variables": { "id": studioId },
            "query": `query FindStudio($id: ID!) {
                findStudio(id: $id) {
                  id
                  stash_ids { endpoint stash_id }
                }
              }`
        };
        const result = await stash.callGQL(reqData);
        return result?.data?.findStudio;
    }

    async function performerPageHandler() {
        if (await isSceneCountEnabled('performers')) {
            const performer = await getPerformer();
            if (!performer) return;
            const data = await stash.getStashBoxes();
            for (const { endpoint, stash_id } of performer.stash_ids) {
                const el = getElementByXpath(`//span[@class='stash-id-pill']/a[text()='${stash_id}']`);
                if (el && !el.parentElement.querySelector('.stashbox-scene-count')) {
                    const badge = createElementFromHTML(`<span class="stashbox-scene-count ml-1" style="display: none;"></span>`);
                    insertAfter(badge, el);
                    const api_key = data.data.configuration.general.stashBoxes.find(o => o.endpoint == endpoint)?.api_key;
                    if (!api_key) continue;
                    await runGetStashboxPerformerSceneCountTask(endpoint, api_key, stash_id);
                    const stashBoxSceneCount = await stash.pollLogsForMessage(`[Plugin / Performer Tools] ${stash_id}: `);
                    badge.innerText = stashBoxSceneCount;
                    badge.style.display = 'inline-block';
                }
            }
        }
    }
    stash.addEventListener('page:performer:any', performerPageHandler);
    stash.addEventListener('page:performer:details:expanded', performerPageHandler);

    async function studioPageHandler() {
        if (await isSceneCountEnabled('studios')) {
            const studio = await getStudio();
            if (!studio) return;
            const data = await stash.getStashBoxes();
            for (const { endpoint, stash_id } of studio.stash_ids) {
                const el = getElementByXpath(`//span[@class='stash-id-pill']/a[text()='${stash_id}']`);
                if (el && !el.parentElement.querySelector('.stashbox-scene-count')) {
                    const badge = createElementFromHTML(`<span class="stashbox-scene-count ml-1" style="display: none;"></span>`);
                    insertAfter(badge, el);
                    const api_key = data.data.configuration.general.stashBoxes.find(o => o.endpoint == endpoint)?.api_key;
                    if (!api_key) continue;
                    await runGetStashboxStudioSceneCountTask(endpoint, api_key, stash_id);
                    const stashBoxSceneCount = await stash.pollLogsForMessage(`[Plugin / Performer Tools] ${stash_id}: `);
                    badge.innerText = stashBoxSceneCount;
                    badge.style.display = 'inline-block';
                }
            }
        }
    }
    stash.addEventListener('page:studio:any', studioPageHandler);
    stash.addEventListener('page:studio:details:expanded', studioPageHandler);

})();
