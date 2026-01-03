(() => {
  const board = document.getElementById("board");
  const results = document.getElementById("results");
  const resetAllBtn = document.getElementById("resetAll");
  const debugToggle = document.getElementById("debugToggle");

  const maxGuesses = parseInt(board.dataset.max, 10) || 5;
  const initialRows = parseInt(board.dataset.initial, 10) || 3;

  const STATES = ["unknown", "green", "yellow", "gray"];

  const state = {
    rows: [],
    debug: false,
  };

  function mkRow(index) {
    return {
      index,
      word: "",
      states: ["unknown", "unknown", "unknown", "unknown", "unknown"],
      locked: false,
      submitted: false,
    };
  }

  function init() {
    state.rows = [];
    for (let i = 0; i < initialRows; i++) state.rows.push(mkRow(i));
    render();
    wire();
  }

  function wire() {
    resetAllBtn.addEventListener("click", () => {
      init();
      results.innerHTML = '<div class="muted">Lock at least one row to see suggestions.</div>';
    });

    debugToggle.addEventListener("change", () => {
      state.debug = debugToggle.checked;
      submit();
    });
  }

  function render() {
    board.innerHTML = "";
    state.rows.forEach((row) => {
      const el = document.createElement("div");
      el.className = "guess-row";
      el.dataset.row = row.index;

      // Input
      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 5;
      input.placeholder = "guess";
      input.value = row.word;
      input.disabled = row.locked;
      input.addEventListener("input", (e) => {
        let v = (e.target.value || "").toLowerCase().replace(/[^a-z]/g, "");
        if (v.length > 5) v = v.slice(0, 5);
        e.target.value = v;
        row.word = v;
        // update tile letters immediately
        syncTilesLetters(row.index);
      });
      el.appendChild(input);

      // Tiles
      for (let i = 0; i < 5; i++) {
        const t = document.createElement("div");
        t.className = "tile";
        t.dataset.pos = String(i);
        t.dataset.state = row.states[i];
        t.textContent = row.word[i] ? row.word[i].toUpperCase() : "";
        if (window.innerWidth <= 720) t.classList.add("small");

        t.addEventListener("click", () => {
          if (row.locked) return;
          // if there's no letter here yet, don't cycle
          if (!row.word[i]) return;
          const cur = row.states[i];
          const next = STATES[(STATES.indexOf(cur) + 1) % STATES.length];
          row.states[i] = next;
          t.dataset.state = next;
        });

        el.appendChild(t);
      }

      // Actions
      const actions = document.createElement("div");
      actions.className = "row-actions";

      const lockBtn = document.createElement("button");
      lockBtn.className = "btn btn-primary";
      lockBtn.type = "button";
      lockBtn.textContent = row.locked ? "Locked" : "Lock";
      lockBtn.disabled = row.locked;

      lockBtn.addEventListener("click", () => {
        if (row.locked) return;
        // validate
        if (!/^[a-z]{5}$/.test(row.word)) {
          flash(results, "Enter a 5-letter word before locking.");
          return;
        }
        // require all 5 tiles to be set (not unknown)
        if (row.states.some((s) => s === "unknown")) {
          flash(results, "Set all tile colors before locking.");
          return;
        }
        row.locked = true;
        row.submitted = true;

        // If fewer than max rows exist, add a new editable row automatically (up to 5).
        if (state.rows.length < maxGuesses) {
          state.rows.push(mkRow(state.rows.length));
        }
        render();
        submit();
      });

      actions.appendChild(lockBtn);

      // show row number
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
    const word = (input && input.value) ? input.value : "";
    const tiles = rowEl.querySelectorAll(".tile");
    tiles.forEach((t) => {
      const pos = parseInt(t.dataset.pos, 10);
      t.textContent = word[pos] ? word[pos].toUpperCase() : "";
    });
  }

  function lockedRowsPayload() {
    const locked = state.rows.filter(r => r.submitted);
    return locked.map(r => ({ word: r.word, states: r.states }));
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

      // If auth expired, server redirects with 303; fetch won't follow as navigation, but will return HTML.
      const html = await resp.text();
      results.innerHTML = html;
    } catch (e) {
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
