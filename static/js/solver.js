(() => {
  const board = document.getElementById("board");
  const results = document.getElementById("results");
  const resetAllBtn = document.getElementById("resetAll");
  const debugToggle = document.getElementById("debugToggle");
  const mobileHint = document.getElementById("mobileHint");

  const maxGuesses = parseInt(board.dataset.max, 10) || 5;
  const initialRows = parseInt(board.dataset.initial, 10) || 3;

  const isMobile = window.matchMedia("(pointer: coarse)").matches;
  const STATES = ["unknown", "green", "yellow", "gray"];

  const state = {
    rows: [],
    debug: false,
    active: { rowIndex: null, pos: null }, // mobile only
  };

  function mkRow(index) {
    return {
      index,
      word: "",
      letters: ["", "", "", "", ""], // mobile source-of-truth
      states: ["unknown", "unknown", "unknown", "unknown", "unknown"],
      mode: "entry", // mobile: entry | marking
      locked: false,
      submitted: false,
    };
  }

  let hiddenKeyInput = null;

  function ensureHiddenKeyInput() {
    if (!isMobile) return;
    if (hiddenKeyInput) return;

    hiddenKeyInput = document.createElement("input");
    hiddenKeyInput.type = "text";
    hiddenKeyInput.autocapitalize = "none";
    hiddenKeyInput.autocomplete = "off";
    hiddenKeyInput.autocorrect = "off";
    hiddenKeyInput.spellcheck = false;

    hiddenKeyInput.style.position = "fixed";
    hiddenKeyInput.style.opacity = "0";
    hiddenKeyInput.style.pointerEvents = "none";
    hiddenKeyInput.style.height = "1px";
    hiddenKeyInput.style.width = "1px";
    hiddenKeyInput.style.left = "-9999px";
    hiddenKeyInput.style.top = "0";

    document.body.appendChild(hiddenKeyInput);

    hiddenKeyInput.addEventListener("keydown", (e) => {
      if (state.active.rowIndex === null || state.active.pos === null) return;

      const row = state.rows[state.active.rowIndex];
      if (!row || row.locked) return;
      if (row.mode !== "entry") return;

      const key = e.key;

      if (key === "Backspace") {
        e.preventDefault();
        handleBackspace(row);
        return;
      }

      if (!/^[a-zA-Z]$/.test(key)) return;

      e.preventDefault();
      setLetter(row, key.toLowerCase());
    });
  }

  function setActive(rowIndex, pos) {
    state.active.rowIndex = rowIndex;
    state.active.pos = pos;
    ensureHiddenKeyInput();
    if (hiddenKeyInput) {
      hiddenKeyInput.value = "";
      hiddenKeyInput.focus({ preventScroll: true });
    }
  }

  function syncRowFromLetters(row) {
    row.word = row.letters.join("");
  }

  function handleBackspace(row) {
    const pos = state.active.pos;
    if (pos === null) return;

    if (row.letters[pos]) {
      row.letters[pos] = "";
      row.states[pos] = "unknown";
      syncRowFromLetters(row);
      render();
      return;
    }

    if (pos > 0) {
      state.active.pos = pos - 1;
      const newPos = state.active.pos;
      if (row.letters[newPos]) {
        row.letters[newPos] = "";
        row.states[newPos] = "unknown";
      }
      syncRowFromLetters(row);
      render();
    }
  }

  function setLetter(row, ch) {
    const pos = state.active.pos;
    if (pos === null) return;

    row.letters[pos] = ch;
    syncRowFromLetters(row);

    if (pos < 4) state.active.pos = pos + 1;
    render();
  }

  function init() {
    state.rows = [];
    for (let i = 0; i < initialRows; i++) state.rows.push(mkRow(i));
    state.debug = false;
    debugToggle.checked = false;
    state.active = { rowIndex: null, pos: null };

    ensureHiddenKeyInput();
    render();
    wireOnce();
    maybeShowMobileHint();

    results.innerHTML = '<div class="muted">Lock at least one row to see suggestions.</div>';
  }

  let wired = false;
  function wireOnce() {
    if (wired) return;
    wired = true;

    resetAllBtn.addEventListener("click", () => init());

    debugToggle.addEventListener("change", () => {
      state.debug = debugToggle.checked;
      submit();
    });
  }

  function maybeShowMobileHint() {
    if (!isMobile || !mobileHint) return;

    const key = "wa_mobile_hint_dismissed_v1";
    if (localStorage.getItem(key) === "1") {
      mobileHint.innerHTML = "";
      return;
    }

    mobileHint.innerHTML = `
      <div class="hint">
        <div class="hint-text">
          <strong>Tip:</strong> Tap a tile to type the word. When you're done, tap <strong>Mark colors</strong>,
          then tap tiles to cycle green/yellow/gray. Use <strong>Edit letters</strong> if you need to fix a typo.
        </div>
        <div class="hint-actions">
          <button id="dismissHint" class="btn btn-ghost" type="button">Got it</button>
        </div>
      </div>
    `;

    const btn = document.getElementById("dismissHint");
    if (btn) {
      btn.addEventListener("click", () => {
        localStorage.setItem(key, "1");
        mobileHint.innerHTML = "";
      });
    }
  }

  function render() {
    board.innerHTML = "";

    state.rows.forEach((row) => {
      const el = document.createElement("div");
      el.className = "guess-row";
      el.dataset.row = String(row.index);

      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 5;
      input.placeholder = "guess";
      input.value = row.word;
      input.disabled = row.locked || (isMobile && row.mode !== "entry");
      input.addEventListener("input", (e) => {
        let v = (e.target.value || "").toLowerCase().replace(/[^a-z]/g, "");
        if (v.length > 5) v = v.slice(0, 5);
        e.target.value = v;
        row.word = v;
        row.letters = [v[0] || "", v[1] || "", v[2] || "", v[3] || "", v[4] || ""];
        syncTilesLetters(row.index);
      });
      el.appendChild(input);

      for (let i = 0; i < 5; i++) {
        const t = document.createElement("div");
        t.className = "tile";
        t.dataset.pos = String(i);
        t.dataset.state = row.states[i];

        const letter = isMobile ? (row.letters[i] || "") : (row.word[i] || "");
        t.textContent = letter ? letter.toUpperCase() : "";

        if (window.innerWidth <= 720) t.classList.add("small");
        if (row.locked) t.classList.add("locked");

        t.addEventListener("click", () => {
          if (row.locked) return;

          if (isMobile) {
            if (row.mode === "entry") {
              setActive(row.index, i);
              return;
            }
            const cur = row.states[i];
            const next = STATES[(STATES.indexOf(cur) + 1) % STATES.length];
            row.states[i] = next;
            t.dataset.state = next;
            return;
          }

          if (!row.word[i]) return;
          const cur = row.states[i];
          const next = STATES[(STATES.indexOf(cur) + 1) % STATES.length];
          row.states[i] = next;
          t.dataset.state = next;
        });

        el.appendChild(t);
      }

      const actions = document.createElement("div");
      actions.className = "row-actions";

      if (isMobile && !row.locked) {
        const lettersComplete = row.word.length === 5 && /^[a-z]{5}$/.test(row.word);

        if (row.mode === "entry") {
          const markBtn = document.createElement("button");
          markBtn.className = "btn btn-primary";
          markBtn.type = "button";
          markBtn.textContent = "Mark colors";
          markBtn.disabled = !lettersComplete;
          markBtn.addEventListener("click", () => {
            if (!lettersComplete) return;
            row.mode = "marking";
            state.active = { rowIndex: null, pos: null };
            if (hiddenKeyInput) hiddenKeyInput.blur();
            render();
          });
          actions.appendChild(markBtn);
        } else {
          const editBtn = document.createElement("button");
          editBtn.className = "btn btn-ghost";
          editBtn.type = "button";
          editBtn.textContent = "Edit letters";
          editBtn.addEventListener("click", () => {
            row.mode = "entry";
            row.states = ["unknown", "unknown", "unknown", "unknown", "unknown"];
            render();
          });
          actions.appendChild(editBtn);
        }
      }

      const lockBtn = document.createElement("button");
      lockBtn.className = "btn btn-primary";
      lockBtn.type = "button";
      lockBtn.textContent = row.locked ? "Locked" : "Lock";
      lockBtn.disabled = row.locked;

      lockBtn.addEventListener("click", () => {
        if (row.locked) return;

        if (!/^[a-z]{5}$/.test(row.word)) {
          flash(results, "Enter a 5-letter word before locking.");
          return;
        }

        if (isMobile && row.mode !== "marking") {
          flash(results, "Tap “Mark colors” first, then set tile colors.");
          return;
        }

        if (row.states.some((s) => s === "unknown")) {
          flash(results, "Set all tile colors before locking.");
          return;
        }

        row.locked = true;
        row.submitted = true;

        if (state.rows.length < maxGuesses) {
          state.rows.push(mkRow(state.rows.length));
        }

        state.active = { rowIndex: null, pos: null };
        if (hiddenKeyInput) hiddenKeyInput.blur();

        render();
        submit();
      });

      actions.appendChild(lockBtn);

      const pill = document.createElement("span");
      pill.className = "pill";
      pill.textContent = `#${row.index + 1}`;
      actions.appendChild(pill);

      el.appendChild(actions);
      board.appendChild(el);
    });
  }

  function syncTilesLetters(rowIndex) {
    const rowEl = board.querySelector(`[data-row="${rowIndex}"]`);
    if (!rowEl) return;
    const input = rowEl.querySelector("input");
    const word = input && input.value ? input.value : "";
    const tiles = rowEl.querySelectorAll(".tile");
    tiles.forEach((t) => {
      const pos = parseInt(t.dataset.pos, 10);
      t.textContent = word[pos] ? word[pos].toUpperCase() : "";
    });
  }

  function lockedRowsPayload() {
    const locked = state.rows.filter((r) => r.submitted);
    return locked.map((r) => ({ word: r.word, states: r.states }));
  }

  async function submit() {
    const payload = { guesses: lockedRowsPayload(), debug: state.debug };
    if (!payload.guesses.length) return;

    try {
      const resp = await fetch("/solve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        credentials: "same-origin",
      });

      const html = await resp.text();
      results.innerHTML = html;
    } catch (_e) {
      flash(results, "Network error.");
    }
  }

  function flash(container, msg) {
    container.innerHTML = `<div class="alert warn">${escapeHtml(msg)}</div>`;
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  init();
})();
