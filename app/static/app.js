(() => {
  const app = document.getElementById('app');
  const toastEl = document.getElementById('toast');

  let toastTimer;
  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('show'), 2200);
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const contentType = response.headers.get('content-type') || '';
    const body = contentType.includes('application/json') ? await response.json() : await response.text();
    if (!response.ok) {
      const message = typeof body === 'object' ? (body.detail || 'Request failed') : body;
      throw new Error(message || `HTTP ${response.status}`);
    }
    return body;
  }

  function setTheme(theme) {
    document.body.dataset.theme = theme;
    app.className = theme.startsWith('setup') ? 'setup-shell' : 'veto-shell';
  }

  function brandMarkup(organizationName) {
    return `<div class="brand" aria-label="${escapeHtml(organizationName)} mapban">
      <span class="brand-copy"><strong>${escapeHtml(organizationName)}</strong><small>mapban</small></span>
    </div>`;
  }

  function logoMarkup(team, className = '') {
    if (!team?.logo) return `<div class="team-logo ${className}"><span>${escapeHtml(team?.tricode || '?')}</span></div>`;
    return `<img class="team-logo ${className}" src="${escapeHtml(team.logo)}" alt="${escapeHtml(team.name)} logo">`;
  }

  function mapImageStyle(map) {
    const id = encodeURIComponent(String(map?.id || '').toLowerCase());
    return `background-image:linear-gradient(180deg,rgba(9,17,28,.04),rgba(9,17,28,.34)),url('/static/maps/${id}.webp')`;
  }

  function teamHero(team, side = '') {
    return `<div class="team-hero ${side}">
      <div class="team-hero-line">${logoMarkup(team, 'hero-logo')}<span class="hero-tricode">${escapeHtml(team.tricode)}</span></div>
      <div class="hero-name">${escapeHtml(team.name)}</div>
    </div>`;
  }

  function setupMatchup(a, b, center = '') {
    return `<div class="setup-matchup">
      ${teamHero(a, 'left')}
      <div class="setup-matchup-center">${center}</div>
      ${teamHero(b, 'right')}
    </div>`;
  }

  async function renderCreate() {
    setTheme('setup');
    let options;
    try {
      options = await api('/api/session-options');
    } catch (error) {
      app.innerHTML = `${brandMarkup('Mapban')}<div class="setup-error-wrap"><div class="error">${escapeHtml(error.message)}</div></div>`;
      return;
    }

    function teamCreatePanel(letter, teamOptions, manualOption) {
      return `<section class="team-select-column">
        <div class="team-slot-label">Team ${letter}</div>
        <div class="field setup-field team-picker-field">
          <input id="team-${letter}-search" class="team-search" type="search" placeholder="Search teams…" aria-label="Search Team ${letter}" autocomplete="off">
          <select id="team-${letter}-select" aria-label="Team ${letter}">
            <option value="">Select team…</option>
            ${manualOption}
            ${teamOptions}
          </select>
        </div>
        <div id="team-${letter}-manual" class="manual-card hidden">
          <label>Team Name<input id="team-${letter}-name" type="text" maxlength="80"></label>
          <label>Tricode<input id="team-${letter}-tricode" type="text" maxlength="12"></label>
          <label>Logo URL<input id="team-${letter}-logo" type="url" maxlength="1000"></label>
        </div>
      </section>`;
    }

    function readTeam(letter) {
      const select = document.getElementById(`team-${letter}-select`);
      if (!select.value) throw new Error(`Select Team ${letter}`);
      if (select.value === '__manual__') {
        const name = document.getElementById(`team-${letter}-name`).value.trim();
        const tricode = document.getElementById(`team-${letter}-tricode`).value.trim();
        const logo = document.getElementById(`team-${letter}-logo`).value.trim();
        if (!name || !tricode) throw new Error(`Manual Team ${letter} requires a name and tricode`);
        return { source: 'manual', name, tricode, logo };
      }
      return { source: 'configured', teamId: select.value };
    }

    const teamOptions = options.teams.map(team =>
      `<option value="${escapeHtml(team.id)}" data-team-option="1" data-search="${escapeHtml(`${team.name} ${team.tricode}`.toLowerCase())}">${escapeHtml(team.name)} (${escapeHtml(team.tricode)})</option>`
    ).join('');
    const manualOption = options.allowManualTeams ? '<option value="__manual__">Manual Entry</option>' : '';

    app.innerHTML = `
      ${brandMarkup(options.organizationName)}
      <section class="setup-stage teams-stage">
        <div class="setup-title-block">
          <h1>Choose Teams</h1>
          <p>Select the two teams for this Mapban session.</p>
        </div>
        <div id="create-error" class="error hidden"></div>
        <div class="team-selection-grid">
          ${teamCreatePanel('A', teamOptions, manualOption)}
          <div class="team-selection-vs">VS</div>
          ${teamCreatePanel('B', teamOptions, manualOption)}
        </div>
        <button id="teams-next" class="spectra-button neutral setup-primary-action">Create Session & Continue</button>
        <div class="setup-step">Step 1 of 2 · Teams</div>
      </section>`;

    ['A', 'B'].forEach(letter => {
      const select = document.getElementById(`team-${letter}-select`);
      const search = document.getElementById(`team-${letter}-search`);

      select.addEventListener('change', () => {
        document.getElementById(`team-${letter}-manual`).classList.toggle('hidden', select.value !== '__manual__');
      });

      search.addEventListener('input', () => {
        const query = search.value.trim().toLowerCase();
        Array.from(select.options).forEach(option => {
          if (option.dataset.teamOption !== '1') {
            option.hidden = false;
            return;
          }
          option.hidden = query !== '' && !(option.dataset.search || '').includes(query);
        });
      });
    });

    document.getElementById('teams-next').addEventListener('click', async () => {
      const errorEl = document.getElementById('create-error');
      errorEl.classList.add('hidden');
      const button = document.getElementById('teams-next');
      try {
        const teams = [readTeam('A'), readTeam('B')];
        if (teams[0].source === 'configured' && teams[1].source === 'configured' && teams[0].teamId === teams[1].teamId) {
          throw new Error('The same configured team cannot occupy both slots');
        }
        button.disabled = true;
        button.textContent = 'Creating…';
        const result = await api('/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ teams }),
        });
        window.location.href = result.adminUrl;
      } catch (error) {
        errorEl.textContent = error.message;
        errorEl.classList.remove('hidden');
        button.disabled = false;
        button.textContent = 'Create Session & Continue';
      }
    });
  }

  function parseSessionPath() {
    const match = location.pathname.match(/^\/session\/([^/]+)\/(admin|team)\/([^/]+)$/);
    if (!match) return null;
    return { sessionId: match[1].toUpperCase(), pageRole: match[2], token: match[3] };
  }

  async function renderSessionRoute(route) {
    let state;
    try {
      state = await api(`/api/sessions/${encodeURIComponent(route.sessionId)}/view?token=${encodeURIComponent(route.token)}`);
    } catch (error) {
      setTheme('live-other');
      app.innerHTML = `<div class="error live-error">${escapeHtml(error.message)}</div>`;
      return;
    }

    let sessionOptions = null;
    if (state.viewer.role === 'admin') {
      try { sessionOptions = await api('/api/session-options'); }
      catch (error) { console.warn('Unable to load setup options', error); }
    }

    let currentState = state;
    let selectedMap = null;
    let selectedSide = null;
    let setupDraft = null;
    let setupSaveChain = Promise.resolve();
    let setupSaveError = null;
    let socket;

    function authHeaders() {
      return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${route.token}` };
    }

    function teamById(id) { return currentState.teams.find(team => team.id === id); }
    function teamBySlot(slot) { return currentState.teams.find(team => team.slot === slot); }

    function ensureSetupDraft() {
      if (!currentState.setupRequired) {
        setupDraft = null;
        return;
      }
      if (!setupDraft) {
        setupDraft = {
          format: currentState.format,
          advantageTeamId: currentState.doubleBanAdvantageTeamId || null,
          mapPool: new Set(currentState.mapPool.map(map => map.id)),
        };
      }
    }

    function setupPayload() {
      return {
        format: setupDraft.format,
        doubleBanAdvantageTeamId: setupDraft.format === 'bo5' ? setupDraft.advantageTeamId : null,
        mapPool: [...setupDraft.mapPool],
      };
    }

    function updateSetupSaveStatus(text, isError = false) {
      const el = document.getElementById('setup-save-status');
      if (!el) return;
      el.textContent = text;
      el.classList.toggle('invalid', isError);
      el.classList.toggle('valid', !isError && text === 'Saved');
    }

    function queueSetupSave() {
      if (!setupDraft || !currentState.setupRequired) return setupSaveChain;
      const payload = setupPayload();
      setupSaveError = null;
      updateSetupSaveStatus('Saving…');
      setupSaveChain = setupSaveChain.then(async () => {
        try {
          const view = await api(`/api/sessions/${currentState.sessionId}/settings`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(payload),
          });
          currentState = view;
          setupSaveError = null;
          updateSetupSaveStatus('Saved');
        } catch (error) {
          setupSaveError = error;
          updateSetupSaveStatus(error.message, true);
          throw error;
        }
      }).catch(() => {});
      return setupSaveChain;
    }

    async function startVeto() {
      const errorEl = document.getElementById('setup-error');
      errorEl?.classList.add('hidden');
      try {
        if (setupDraft.mapPool.size !== 7) throw new Error('Exactly seven maps are required before Start Veto');
        await queueSetupSave();
        await setupSaveChain;
        if (setupSaveError) throw setupSaveError;
        const button = document.getElementById('start-veto');
        if (button) { button.disabled = true; button.textContent = 'Starting…'; }
        const view = await api(`/api/sessions/${currentState.sessionId}/start`, {
          method: 'POST',
          headers: authHeaders(),
          body: '{}',
        });
        currentState = view;
        setupDraft = null;
        render();
      } catch (error) {
        if (errorEl) {
          errorEl.textContent = error.message;
          errorEl.classList.remove('hidden');
        } else toast(error.message);
        const button = document.getElementById('start-veto');
        if (button) { button.disabled = setupDraft?.mapPool.size !== 7; button.textContent = 'Start Veto'; }
      }
    }

    function render() {
      ensureSetupDraft();
      if (currentState.viewer.role === 'admin' && currentState.setupRequired) {
        renderAdminSetup();
        return;
      }
      if (currentState.viewer.role === 'team' && currentState.setupRequired) {
        renderTeamWaitingForSetup();
        return;
      }
      if (currentState.viewer.role === 'admin' && !currentState.currentTurn) {
        renderAdminComplete();
        return;
      }
      renderLiveVeto();
    }

    function renderAdminSetup() {
      setTheme('setup');
      if (!sessionOptions) {
        app.innerHTML = `${brandMarkup(currentState.organizationName)}<div class="setup-error-wrap"><div class="error">Unable to load maps.json configuration. Refresh after checking the server config.</div></div>`;
        return;
      }
      const a = teamBySlot(0);
      const b = teamBySlot(1);
      const draft = setupDraft;
      if (draft.format !== 'bo5') draft.advantageTeamId = null;

      const mapCards = sessionOptions.maps.map(map => `
        <label class="setup-map-card ${draft.mapPool.has(map.id) ? 'selected' : ''}">
          <input type="checkbox" value="${escapeHtml(map.id)}" ${draft.mapPool.has(map.id) ? 'checked' : ''}>
          <span class="setup-map-art" style="${mapImageStyle(map)}"><span class="map-fallback">${escapeHtml(map.name)}</span></span>
          <span class="setup-map-name">${escapeHtml(map.name)}</span>
        </label>`).join('');

      app.innerHTML = `
        ${brandMarkup(currentState.organizationName)}
        <section class="setup-stage format-stage">
          ${setupMatchup(a, b, `<button id="setup-swap" class="spectra-button mini neutral">⇄ Swap Teams</button>`)}
          <div id="setup-error" class="error hidden centered-error"></div>

          <section class="setup-choice-block format-block">
            <h1>Choose Format</h1>
            <p>Select the format for this Mapban session.</p>
            <div class="format-buttons">
              <label class="format-option bo1"><input type="radio" name="setup-format" value="bo1" ${draft.format === 'bo1' ? 'checked' : ''}><span>Best of 1</span></label>
              <label class="format-option bo3"><input type="radio" name="setup-format" value="bo3" ${draft.format === 'bo3' ? 'checked' : ''}><span>Best of 3</span></label>
              <label class="format-option bo5"><input type="radio" name="setup-format" value="bo5" ${draft.format === 'bo5' ? 'checked' : ''}><span>Best of 5</span></label>
            </div>
            <div id="setup-advantage-wrap" class="advantage-wrap ${draft.format === 'bo5' ? '' : 'hidden'}">
              <span>Double map ban advantage</span>
              <select id="setup-advantage">
                <option value="" ${!draft.advantageTeamId ? 'selected' : ''}>None</option>
                <option value="${escapeHtml(a.id)}" ${draft.advantageTeamId === a.id ? 'selected' : ''}>${escapeHtml(a.name)}</option>
                <option value="${escapeHtml(b.id)}" ${draft.advantageTeamId === b.id ? 'selected' : ''}>${escapeHtml(b.name)}</option>
              </select>
            </div>
          </section>

          <section class="setup-choice-block map-pool-block">
            <h2>Choose Maps</h2>
            <p>Highlight all maps you want in the map pool.</p>
            <div id="setup-map-grid" class="setup-map-grid">${mapCards}</div>
            <div class="map-pool-actions">
              <button id="setup-restore-pool" class="spectra-button competitive">Competitive</button>
              <span id="setup-pool-count" class="pool-count"></span>
              <span id="setup-save-status" class="autosave-state">Saved</span>
            </div>
          </section>

          <div class="setup-launch-row">
            <button id="start-veto" class="spectra-button neutral large">Start Mapban</button>
            <button id="reset-veto" class="text-button danger-text">Reset Veto</button>
          </div>

          ${adminLinksMarkup(false, true)}
          <div class="setup-step">Step 2 of 2 · Format & Map Pool</div>
        </section>`;

      const mapGrid = document.getElementById('setup-map-grid');
      const syncCount = () => {
        draft.mapPool = new Set([...mapGrid.querySelectorAll('input:checked')].map(box => box.value));
        mapGrid.querySelectorAll('.setup-map-card').forEach(card => {
          card.classList.toggle('selected', card.querySelector('input').checked);
        });
        const count = draft.mapPool.size;
        const el = document.getElementById('setup-pool-count');
        el.textContent = `${count} / 7 selected`;
        el.className = `pool-count ${count === 7 ? 'valid' : 'invalid'}`;
        document.getElementById('start-veto').disabled = count !== 7;
      };

      document.querySelectorAll('input[name="setup-format"]').forEach(input => input.addEventListener('change', event => {
        draft.format = event.target.value;
        if (draft.format !== 'bo5') draft.advantageTeamId = null;
        queueSetupSave();
        renderAdminSetup();
      }));
      document.getElementById('setup-advantage')?.addEventListener('change', event => {
        draft.advantageTeamId = event.target.value || null;
        queueSetupSave();
      });
      mapGrid.addEventListener('change', () => { syncCount(); queueSetupSave(); });
      document.getElementById('setup-restore-pool').addEventListener('click', () => {
        draft.mapPool = new Set(sessionOptions.defaultPool);
        queueSetupSave();
        renderAdminSetup();
      });
      document.getElementById('setup-swap').addEventListener('click', swapTeams);
      document.getElementById('start-veto').addEventListener('click', startVeto);
      document.getElementById('reset-veto').addEventListener('click', reset);
      bindCopyButtons();
      syncCount();
    }

    function renderTeamWaitingForSetup() {
      setTheme('live-other');
      const viewerTeam = teamById(currentState.viewer.teamId);
      app.innerHTML = `
        ${brandMarkup(currentState.organizationName)}
        <div class="turn-banner">
          <h1>Waiting for Producer</h1>
          <p>Format and map pool are being configured</p>
        </div>
        <section class="waiting-stage">
          <div class="mapban-state-title">Mapban State</div>
          <p class="state-empty">The veto has not started yet.</p>
          <div class="waiting-team">${logoMarkup(viewerTeam, 'waiting-logo')}<strong>${escapeHtml(viewerTeam?.name || '')}</strong><span>Your team link is ready and will update automatically.</span></div>
        </section>`;
    }

    function turnText(turn) {
      if (!turn) return { action: 'Mapban complete', progress: 'All maps and sides have been selected.' };
      if (turn.phase === 'side') return { action: 'Choose your starting side', progress: 'Choosing starting side' };
      if (turn.mapAction === 'ban') return { action: turn.kind === 'side_ban' ? 'Ban the next map' : 'Ban a map', progress: 'Banning a map' };
      if (turn.mapAction === 'pick') return { action: turn.kind === 'side_pick' ? 'Select the next map' : 'Select a map', progress: 'Selecting a map' };
      return { action: 'Make a selection', progress: 'Making a selection' };
    }

    function liveTurnHeader(turn) {
      if (!turn) return `<div class="turn-banner"><h1>Mapban Complete</h1><p>All maps and sides have been selected</p></div>`;
      const actor = teamById(turn.teamId);
      const text = turnText(turn);
      if (currentState.viewer.role === 'team') {
        const mine = currentState.viewer.teamId === turn.teamId;
        return `<div class="turn-banner"><h1>${mine ? 'Your Teams Turn' : 'Other Teams Turn'}</h1><p>${escapeHtml(mine ? text.action : text.progress)}</p></div>`;
      }
      return `<div class="turn-banner producer-turn"><h1>${escapeHtml(actor.name)}'s Turn</h1><p>${escapeHtml(text.action)}</p></div>`;
    }

    function resolvedMapCard(map) {
      const actor = map.status === 'banned' ? teamById(map.bannedByTeamId) : map.status === 'picked' ? teamById(map.pickedByTeamId) : null;
      let meta = '';
      if (map.status === 'banned') meta = `Banned by ${actor?.tricode || ''}`;
      if (map.status === 'picked') {
        if (map.pickedAttack == null) meta = `${teamById(map.sidePickedByTeamId)?.tricode || ''} picking…`;
        else meta = `${teamById(map.sidePickedByTeamId)?.tricode || ''} picks ${map.pickedAttack ? 'ATK' : 'DEF'}`;
      }
      if (map.status === 'decider') {
        if (map.pickedAttack == null) meta = `${teamById(map.sidePickedByTeamId)?.tricode || ''} picking…`;
        else meta = `${teamById(map.sidePickedByTeamId)?.tricode || ''} picks ${map.pickedAttack ? 'ATK' : 'DEF'}`;
      }
      const logo = actor ? logoMarkup(actor, 'state-team-logo') : '';
      const showTeamResult = currentState.viewer.role === 'team' && !currentState.currentTurn && (map.status === 'picked' || map.status === 'decider');
      const score = Array.isArray(map.score) && map.score.length === 2 ? map.score : [null, null];
      const a = teamBySlot(0);
      const b = teamBySlot(1);
      const scoreReady = score[0] != null && score[1] != null;
      let resultMarkup = '';
      if (showTeamResult) {
        if (!scoreReady) {
          resultMarkup = '<div class="state-map-score pending"><em>Score pending</em></div>';
        } else {
          const viewerTeam = teamById(currentState.viewer.teamId);
          const viewerSlot = viewerTeam?.slot === 1 ? 1 : 0;
          const opponentSlot = viewerSlot === 0 ? 1 : 0;
          const leftTeam = teamBySlot(viewerSlot);
          const rightTeam = teamBySlot(opponentSlot);
          resultMarkup = `<div class="state-map-score complete"><span>${escapeHtml(leftTeam.tricode)}</span><strong>${escapeHtml(score[viewerSlot])} – ${escapeHtml(score[opponentSlot])}</strong><span>${escapeHtml(rightTeam.tricode)}</span></div>`;
        }
      }
      return `<div class="state-map-card ${escapeHtml(map.status)}">
        <div class="state-map-art" style="${mapImageStyle(map)}"></div>
        <div class="state-map-name">${logo}<span>${escapeHtml(map.name)}</span></div>
        <div class="state-map-meta">${escapeHtml(meta)}</div>
        ${resultMarkup}
      </div>`;
    }

    function mapbanStateMarkup() {
      const resolved = currentState.maps
        .filter(map => map.status !== 'available')
        .sort((a, b) => (a.order ?? 999) - (b.order ?? 999));
      if (!resolved.length) return `<section class="mapban-state"><h2>Mapban State</h2><p class="state-empty">Waiting on first selection...</p></section>`;
      return `<section class="mapban-state"><h2>Mapban State</h2><div class="state-map-row">${resolved.map(resolvedMapCard).join('')}</div></section>`;
    }

    function availableMapCard(map, interactive) {
      if (interactive) {
        return `<button class="available-map-card" data-map="${escapeHtml(map.id)}">
          <span class="available-map-art" style="${mapImageStyle(map)}"><span class="map-fallback">${escapeHtml(map.name)}</span></span>
          <span class="available-map-name">${escapeHtml(map.name)}</span>
        </button>`;
      }
      return `<div class="available-map-card static">
        <span class="available-map-art" style="${mapImageStyle(map)}"><span class="map-fallback">${escapeHtml(map.name)}</span></span>
        <span class="available-map-name">${escapeHtml(map.name)}</span>
      </div>`;
    }

    function actionAreaMarkup(turn) {
      if (!turn) return '';
      const actor = teamById(turn.teamId);
      const canAct = currentState.permissions.canAct;
      const waiting = currentState.viewer.role === 'team' && currentState.viewer.teamId !== turn.teamId;
      const interactiveMaps = canAct && turn.phase === 'map' && Boolean(turn.mapAction);
      const cards = currentState.availableMaps.map(map => availableMapCard(map, interactiveMaps)).join('');
      const mapTitle = turn.kind === 'final_side' ? 'Remaining Map' : 'Available Maps';

      let controls = '';
      if (canAct && turn.phase === 'side' && turn.sideMap) {
        controls += `<div class="side-action-row">
          <button class="side-choice attack" data-side="attack">Choose Attack</button>
          <button class="side-choice defense" data-side="defense">Choose Defense</button>
        </div>`;
      } else if (canAct && turn.mapAction) {
        const label = turn.mapAction === 'ban' ? 'Ban Map' : 'Select Map';
        controls += `<button id="confirm-action" class="spectra-button live-confirm ${turn.mapAction}">${label}</button>`;
      } else if (waiting) {
        controls += `<p class="waiting-other">Waiting for ${escapeHtml(actor.name)}...</p>`;
      }

      return `<section class="available-section">
        <h2>${mapTitle}</h2>
        ${turn.sideMap ? `<p class="side-map-note">Choose starting side on <strong>${escapeHtml(turn.sideMap.name)}</strong>.</p>` : ''}
        ${cards ? `<div class="available-map-row">${cards}</div>` : ''}
        ${controls}
      </section>`;
    }

    function producerToolsMarkup() {
      return `<details class="producer-tools">
        <summary>Producer Controls</summary>
        <div class="producer-tools-grid">
          <section><h3>Veto History</h3>${historyMarkup()}</section>
          <section><h3>Session</h3>${compactLinksMarkup()}</section>
        </div>
        <div class="producer-actions">
          <button id="undo-action" class="spectra-button neutral" ${currentState.permissions.canUndo ? '' : 'disabled'}>Undo Last Action</button>
          <button id="reset-veto" class="spectra-button danger">Reset Veto</button>
        </div>
      </details>`;
    }

    function renderLiveVeto() {
      const turn = currentState.currentTurn;
      const viewerMine = currentState.viewer.role === 'team' && turn && currentState.viewer.teamId === turn.teamId;
      setTheme(viewerMine ? 'live-self' : 'live-other');
      app.innerHTML = `
        ${brandMarkup(currentState.organizationName)}
        ${liveTurnHeader(turn)}
        <main class="live-stage">
          ${mapbanStateMarkup()}
          ${actionAreaMarkup(turn)}
          ${currentState.viewer.role === 'admin' ? `${scoreEditorMarkup(false)}${producerToolsMarkup()}` : ''}
        </main>`;
      bindSessionControls();
    }

    function renderAdminComplete() {
      setTheme('setup');
      const a = teamBySlot(0);
      const b = teamBySlot(1);
      app.innerHTML = `
        ${brandMarkup(currentState.organizationName)}
        <section class="setup-stage complete-stage">
          ${setupMatchup(a, b, '')}
          ${currentState.permissions.discordWebhookEnabled ? `
            <section class="discord-publish-block">
              <div><h2>Share the map veto</h2><p>Post the picks, bans, and starting sides to Discord. Scores are omitted.</p></div>
              <button id="publish-discord" class="spectra-button neutral" ${currentState.permissions.canPublishDiscord ? '' : 'disabled'}>${currentState.discordPublishedAt ? 'Published to Discord' : 'Publish to Discord'}</button>
            </section>` : ''}
          <section class="complete-score-block">
            <h1>All Maps Selected</h1>
            <p>You can now enter scores once maps are completed.</p>
            ${scoreEditorMarkup(true)}
          </section>
          ${currentState.links.spectraOverlayUrl ? `<section class="overlay-url-block"><h2>Overlay URL</h2>${linkRow('Spectra Overlay', currentState.links.spectraOverlayUrl, true)}</section>` : ''}
          ${producerToolsMarkup()}
        </section>`;
      bindSessionControls();
    }

    function scoreEditorMarkup(completeMode = false) {
      if (!currentState.permissions.canEditScores) return '';
      const scoreable = currentState.maps
        .filter(map => map.status === 'picked' || map.status === 'decider')
        .sort((a, b) => (a.order ?? 999) - (b.order ?? 999));
      if (!scoreable.length) return '';
      const a = teamBySlot(0);
      const b = teamBySlot(1);
      const rows = scoreable.map(map => {
        const score = Array.isArray(map.score) && map.score.length === 2 ? map.score : [null, null];
        const sidePicker = map.sidePickedByTeamId ? teamById(map.sidePickedByTeamId) : null;
        let aSide = null;
        let bSide = null;
        if (sidePicker && map.pickedAttack != null) {
          const pickerStartsAttack = Boolean(map.pickedAttack);
          const pickerSlot = sidePicker.slot;
          aSide = pickerSlot === 0 ? (pickerStartsAttack ? 'Attack' : 'Defense') : (pickerStartsAttack ? 'Defense' : 'Attack');
          bSide = aSide === 'Attack' ? 'Defense' : 'Attack';
        }
        const sides = aSide && bSide
          ? `<div class="score-side-row" aria-label="Starting sides for ${escapeHtml(map.name)}">
              <span class="score-side-pill ${aSide.toLowerCase()}"><strong>${escapeHtml(a.tricode)}</strong> starts ${aSide}</span>
              <span class="score-side-pill ${bSide.toLowerCase()}"><strong>${escapeHtml(b.tricode)}</strong> starts ${bSide}</span>
            </div>`
          : `<div class="score-side-pending">Starting sides pending</div>`;
        return `<div class="score-map-row">
          <div class="score-map-name">${escapeHtml(map.name)}</div>
          ${sides}
          <div class="score-input-row">
            <input class="score-input" type="number" min="0" max="99" inputmode="numeric" data-score-map="${escapeHtml(map.id)}" data-score-slot="0" placeholder="${escapeHtml(a.tricode)} Score" aria-label="${escapeHtml(a.name)} score on ${escapeHtml(map.name)}" value="${score[0] == null ? '' : escapeHtml(score[0])}">
            <span>–</span>
            <input class="score-input" type="number" min="0" max="99" inputmode="numeric" data-score-map="${escapeHtml(map.id)}" data-score-slot="1" placeholder="${escapeHtml(b.tricode)} Score" aria-label="${escapeHtml(b.name)} score on ${escapeHtml(map.name)}" value="${score[1] == null ? '' : escapeHtml(score[1])}">
          </div>
        </div>`;
      }).join('');
      return `<section class="score-editor ${completeMode ? 'complete-score-editor' : 'live-score-editor'}">
        ${rows}
        <button id="save-scores" class="spectra-button score-save">Update Scores</button>
      </section>`;
    }

    async function saveScores() {
      const maps = new Map();
      document.querySelectorAll('[data-score-map]').forEach(input => {
        const mapId = input.dataset.scoreMap;
        if (!maps.has(mapId)) maps.set(mapId, [null, null]);
        const raw = input.value.trim();
        maps.get(mapId)[Number(input.dataset.scoreSlot)] = raw === '' ? null : Number(raw);
      });
      const scores = [...maps.entries()].map(([mapId, score]) => ({ mapId, score }));
      const button = document.getElementById('save-scores');
      try {
        if (button) { button.disabled = true; button.textContent = 'Saving…'; }
        const view = await api(`/api/sessions/${currentState.sessionId}/scores`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ scores }),
        });
        currentState = view;
        toast('Scores saved');
        render();
      } catch (error) {
        toast(error.message);
        if (button) { button.disabled = false; button.textContent = 'Update Scores'; }
      }
    }

    async function publishDiscord() {
      const button = document.getElementById('publish-discord');
      if (!button || !currentState.permissions.canPublishDiscord) return;
      button.disabled = true;
      button.textContent = 'Publishing…';
      try {
        currentState = await api(`/api/sessions/${currentState.sessionId}/publish-discord`, {
          method: 'POST',
          headers: authHeaders(),
        });
        render();
        toast('Map veto posted to Discord');
      } catch (error) {
        toast(error.message);
        button.disabled = false;
        button.textContent = 'Publish to Discord';
      }
    }

    function historyMarkup() {
      if (!currentState.history.length) return '<p class="muted">No veto actions yet.</p>';
      return `<div class="history">${currentState.history.map(item => `<div class="history-item"><strong>${escapeHtml(item.summary)}</strong><span>${item.submitted_by === 'admin' ? 'Producer' : 'Team'}</span></div>`).join('')}</div>`;
    }

    function adminLinksMarkup(includeActions = true, setupMode = false) {
      const links = currentState.links;
      const rows = [linkRow('Admin', links.adminUrl, setupMode)];
      links.teamUrls.forEach(item => rows.push(linkRow(item.name, item.url, setupMode)));
      if (links.spectraOverlayUrl) rows.push(linkRow('Spectra Overlay', links.spectraOverlayUrl, setupMode));
      rows.push(linkRow('Session ID', currentState.sessionId, setupMode));
      const actions = includeActions ? `<div class="session-link-actions"><button id="undo-action" class="spectra-button neutral" ${currentState.permissions.canUndo ? '' : 'disabled'}>Undo Last Action</button><button id="reset-veto" class="spectra-button danger">Reset Veto</button></div>` : '';
      return `<section class="session-links ${setupMode ? 'setup-session-links' : ''}"><h2>Session Links</h2><div class="session-link-grid">${rows.join('')}</div>${actions}</section>`;
    }

    function compactLinksMarkup() {
      const links = currentState.links;
      const rows = [];
      rows.push(linkRow('Admin', links.adminUrl, false));
      links.teamUrls.forEach(item => rows.push(linkRow(item.name, item.url, false)));
      if (links.spectraOverlayUrl) rows.push(linkRow('Spectra Overlay', links.spectraOverlayUrl, false));
      rows.push(linkRow('Session ID', currentState.sessionId, false));
      return `<div class="compact-links">${rows.join('')}</div>`;
    }

    function linkRow(label, value, setupMode = false) {
      return `<div class="session-link-row ${setupMode ? 'setup-link-row' : ''}">
        <label>${escapeHtml(label)}</label>
        <div><input type="text" readonly value="${escapeHtml(value)}"><button class="copy-btn" data-copy="${escapeHtml(value)}" aria-label="Copy ${escapeHtml(label)}">⧉</button></div>
      </div>`;
    }

    function bindCopyButtons() {
      document.querySelectorAll('.copy-btn').forEach(button => button.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(button.dataset.copy);
          toast('Copied');
        } catch {
          const input = button.previousElementSibling;
          input.select();
          document.execCommand('copy');
          toast('Copied');
        }
      }));
    }

    function bindSessionControls() {
      document.querySelectorAll('[data-map]').forEach(button => button.addEventListener('click', () => {
        selectedMap = button.dataset.map;
        document.querySelectorAll('[data-map]').forEach(item => item.classList.toggle('selected', item.dataset.map === selectedMap));
      }));
      document.querySelectorAll('[data-side]').forEach(button => button.addEventListener('click', () => submitSide(button.dataset.side, button)));
      document.getElementById('confirm-action')?.addEventListener('click', submitAction);
      document.getElementById('undo-action')?.addEventListener('click', undo);
      document.getElementById('reset-veto')?.addEventListener('click', reset);
      document.getElementById('save-scores')?.addEventListener('click', saveScores);
      document.getElementById('publish-discord')?.addEventListener('click', publishDiscord);
      bindCopyButtons();
    }

    async function submitSide(side, button) {
      const turn = currentState.currentTurn;
      if (!turn || turn.phase !== 'side') return;
      try {
        document.querySelectorAll('[data-side]').forEach(item => { item.disabled = true; });
        button?.classList.add('selected');
        const view = await api(`/api/sessions/${currentState.sessionId}/actions`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ mapId: null, side }),
        });
        currentState = view;
        selectedMap = null;
        selectedSide = null;
        render();
      } catch (error) {
        toast(error.message);
        document.querySelectorAll('[data-side]').forEach(item => { item.disabled = false; });
      }
    }

    async function submitAction() {
      const turn = currentState.currentTurn;
      if (!turn || turn.phase !== 'map' || !turn.mapAction) return;
      if (!selectedMap) return toast(`Choose a map to ${turn.mapAction}`);
      try {
        const view = await api(`/api/sessions/${currentState.sessionId}/actions`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ mapId: selectedMap, side: null }),
        });
        currentState = view;
        selectedMap = null;
        selectedSide = null;
        render();
      } catch (error) { toast(error.message); }
    }

    async function swapTeams() {
      try {
        const view = await api(`/api/sessions/${currentState.sessionId}/swap`, { method: 'POST', headers: authHeaders(), body: '{}' });
        currentState = view;
        setupDraft = null;
        render();
      } catch (error) { toast(error.message); }
    }

    async function undo() {
      if (!confirm('Undo the last veto action?')) return;
      try {
        const view = await api(`/api/sessions/${currentState.sessionId}/undo`, { method: 'POST', headers: authHeaders(), body: '{}' });
        currentState = view;
        render();
      } catch (error) { toast(error.message); }
    }

    async function reset() {
      if (!confirm('Reset the veto and return to Format & Map Pool setup? All veto actions and map scores will be cleared. Teams and session links will stay the same.')) return;
      try {
        const view = await api(`/api/sessions/${currentState.sessionId}/reset`, { method: 'POST', headers: authHeaders(), body: '{}' });
        currentState = view;
        setupDraft = null;
        selectedMap = null;
        selectedSide = null;
        render();
      } catch (error) { toast(error.message); }
    }

    function connectSocket() {
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      socket = new WebSocket(`${scheme}://${location.host}/ws/session/${encodeURIComponent(route.sessionId)}?token=${encodeURIComponent(route.token)}`);
      socket.onmessage = event => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'session_state') {
            const wasSetup = currentState.setupRequired;
            currentState = message.data;
            if (!currentState.setupRequired) setupDraft = null;
            else if (!wasSetup) setupDraft = null;
            selectedMap = null;
            selectedSide = null;
            render();
          }
        } catch { /* ignore malformed messages */ }
      };
      socket.onclose = () => setTimeout(connectSocket, 2000);
    }

    render();
    connectSocket();
  }

  const route = parseSessionPath();
  if (route) renderSessionRoute(route);
  else renderCreate();
})();
