// Companion layer: users, persona, durable facts, chat and proactive check-ins.
import { h, clear, toast, badge, fullTime, jsonBlock } from "../dom.js";
import * as api from "../api.js";
import { reportError } from "../app.js";

function field(label, input) {
  return h("label", { class: "field-label" }, label, input);
}

export async function renderCompanion(root) {
  const state = { userId: null, conversationId: null };
  const usersCard = h("section", { class: "card" });
  const detail = h("div");

  async function loadUsers() {
    clear(usersCard);
    usersCard.append(h("h2", null, "Companion users"));
    let users;
    try {
      users = (await api.listCompanionUsers()).users;
    } catch (error) {
      reportError(error, "could not list companion users");
      usersCard.append(h("p", { class: "muted" }, "Listing failed - check the companion:read scope."));
      return;
    }
    if (!users.length) usersCard.append(h("p", { class: "muted" }, "No companion users yet. Create the first one below."));
    const list = h("div", { class: "row", style: "flex-wrap:wrap;gap:0.4rem" });
    for (const user of users) {
      list.append(h("button", {
        class: `button ${user.user_id === state.userId ? "" : "button-quiet"}`,
        type: "button",
        onclick: () => selectUser(user.user_id),
      }, `${user.display_name} (${user.user_id})`));
    }
    usersCard.append(list);
    const newId = h("input", { class: "input", placeholder: "user id (letters, digits, .-_)", maxlength: 80 });
    const newName = h("input", { class: "input", placeholder: "display name", maxlength: 120 });
    const newTz = h("input", { class: "input", placeholder: "IANA timezone, e.g. Asia/Calcutta", value: "UTC", maxlength: 60 });
    usersCard.append(
      h("h3", null, "New user"),
      field("User ID", newId), field("Display name", newName), field("Timezone", newTz),
      h("button", { class: "button", type: "button", onclick: async () => {
        const userId = newId.value.trim();
        if (!userId || !newName.value.trim()) { toast("User ID and display name are required.", "warn"); return; }
        try {
          await api.upsertCompanionUser(userId, { display_name: newName.value.trim(), timezone: newTz.value.trim() || "UTC" });
          toast(`Companion user ${userId} saved.`, "ok");
          await loadUsers();
          selectUser(userId);
        } catch (error) { reportError(error, "user creation failed"); }
      } }, "Create user"),
    );
  }

  async function selectUser(userId) {
    state.userId = userId;
    state.conversationId = null;
    await loadUsers();
    clear(detail);
    let user;
    try {
      user = await api.getCompanionUser(userId);
    } catch (error) {
      reportError(error, "could not load user");
      return;
    }
    detail.append(profileCard(user), personaCard(user), factsCard(user), conversationsCard(user), checkinsCard(user));
  }

  function profileCard(user) {
    const card = h("section", { class: "card", "data-testid": "companion-profile" }, h("h2", null, "Profile"));
    const name = h("input", { class: "input", name: "profile-display-name", value: user.display_name, maxlength: 120 });
    const tz = h("input", { class: "input", name: "profile-timezone", value: user.timezone || "UTC", maxlength: 60 });
    const meta = h("p", { class: "muted small" }, `created ${fullTime(user.created_at)} · updated ${fullTime(user.updated_at)}`);
    card.append(
      field("Display name", name), field("Timezone (IANA)", tz), meta,
      h("button", { class: "button", type: "button", "data-testid": "save-profile", onclick: async () => {
        const displayName = name.value.trim();
        const timezone = tz.value.trim() || "UTC";
        if (!displayName) { toast("Display name is required.", "warn"); return; }
        if (!window.confirm(`Save profile for ${user.user_id}? Quiet hours and check-in times follow the timezone.`)) return;
        try {
          // PUT /users/{id} replaces the whole profile, so resend the current persona and check-ins unchanged.
          await api.upsertCompanionUser(user.user_id, {
            display_name: displayName, timezone, persona: user.persona, checkins: user.checkins,
          });
          toast("Profile saved.", "ok");
          await selectUser(user.user_id);
        } catch (error) { reportError(error, "profile save failed"); }
      } }, "Save profile"));
    return card;
  }

  function personaCard(user) {
    const persona = user.persona;
    const name = h("input", { class: "input", value: persona.display_name, maxlength: 60 });
    const tone = h("input", { class: "input", value: persona.tone, maxlength: 200 });
    const language = h("input", { class: "input", value: persona.language, maxlength: 20 });
    const emoji = h("input", { type: "checkbox", checked: persona.use_emoji });
    const rules = h("textarea", { class: "input", rows: 3, placeholder: "one style rule per line" });
    rules.value = (persona.style_rules || []).join("\n");
    const custom = h("textarea", { class: "input", rows: 3, maxlength: 2000, placeholder: "custom instructions" });
    custom.value = persona.custom_instructions || "";
    return h("section", { class: "card" },
      h("h2", null, `Persona - ${user.display_name}`),
      field("Persona name", name), field("Tone", tone), field("Language", language),
      h("label", { class: "check" }, emoji, " use emoji sparingly"),
      field("Style rules", rules), field("Custom instructions", custom),
      h("button", { class: "button", type: "button", onclick: async () => {
        if (!window.confirm(`Update the persona for ${user.user_id}? Every later reply uses it.`)) return;
        const body = {
          display_name: name.value.trim() || "Meemee",
          tone: tone.value.trim() || "warm, direct and honest",
          language: language.value.trim() || "en",
          use_emoji: emoji.checked,
          style_rules: rules.value.split("\n").map((r) => r.trim()).filter(Boolean),
          custom_instructions: custom.value,
        };
        try {
          await api.updateCompanionPersona(user.user_id, body);
          toast("Persona updated.", "ok");
        } catch (error) { reportError(error, "persona update failed"); }
      } }, "Save persona"));
  }

  function factsCard(user) {
    const card = h("section", { class: "card" }, h("h2", null, "Durable facts"));
    const listBox = h("div");
    const query = h("input", { class: "input", placeholder: "search facts (full text)" });
    async function reload() {
      clear(listBox);
      let facts;
      try {
        facts = (await api.listCompanionFacts(user.user_id, query.value.trim() || null)).facts;
      } catch (error) { reportError(error, "fact listing failed"); return; }
      if (!facts.length) { listBox.append(h("p", { class: "muted" }, "No active facts.")); return; }
      const table = h("table", { class: "table" },
        h("tr", null, h("th", null, "fact"), h("th", null, "category"), h("th", null, "source"), h("th", null, "")));
      for (const fact of facts) {
        table.append(h("tr", null,
          h("td", null, fact.text),
          h("td", null, badge(fact.category, "info")),
          h("td", { class: "muted small" }, fact.source),
          h("td", null, h("button", { class: "button button-quiet", type: "button", onclick: async () => {
            if (!window.confirm(`Retire fact #${fact.id}? It stays in history but leaves the prompt.`)) return;
            try {
              await api.retireCompanionFact(user.user_id, fact.id);
              toast("Fact retired.", "ok");
              reload();
            } catch (error) { reportError(error, "retire failed"); }
          } }, "retire"))));
      }
      listBox.append(table);
    }
    const category = h("input", { class: "input", value: "general", maxlength: 40 });
    const text = h("input", { class: "input", placeholder: "new fact text", maxlength: 1000 });
    card.append(
      h("div", { class: "row" }, query,
        h("button", { class: "button button-quiet", type: "button", onclick: reload }, "Search")),
      listBox,
      h("h3", null, "Add fact"),
      field("Category", category), field("Text", text),
      h("button", { class: "button", type: "button", onclick: async () => {
        if (!text.value.trim()) { toast("Fact text is required.", "warn"); return; }
        try {
          await api.addCompanionFact(user.user_id, category.value.trim() || "general", text.value.trim());
          text.value = "";
          toast("Fact recorded.", "ok");
          reload();
        } catch (error) { reportError(error, "add fact failed"); }
      } }, "Add fact"));
    reload();
    return card;
  }

  function conversationsCard(user) {
    const card = h("section", { class: "card", "data-testid": "companion-conversations" }, h("h2", null, "Conversations"));
    const listBox = h("div", { class: "row", style: "flex-wrap:wrap;gap:0.4rem" });
    const header = h("p", { class: "muted small" });
    const logBox = h("div", { class: "chat-log", "data-testid": "conversation-log" });
    const input = h("input", { class: "input", name: "chat-input", placeholder: `message ${user.display_name}'s companion…`, maxlength: 8000 });
    let conversations = [];
    let limit = 100;
    async function reloadList() {
      clear(listBox);
      try {
        conversations = (await api.listCompanionConversations(user.user_id)).conversations;
      } catch (error) { reportError(error, "conversation listing failed"); return; }
      if (!conversations.length) {
        listBox.append(h("p", { class: "muted" }, "No conversations yet. Send the first message below."));
        state.conversationId = null;
        clear(logBox); header.textContent = "";
        return;
      }
      if (!conversations.some((c) => c.id === state.conversationId)) state.conversationId = conversations[0].id;
      for (const conversation of conversations) {
        listBox.append(h("button", {
          class: `button ${conversation.id === state.conversationId ? "" : "button-quiet"}`,
          type: "button", "data-conversation-id": conversation.id,
          onclick: () => { state.conversationId = conversation.id; limit = 100; reloadList(); },
        }, `${conversation.channel} · ${fullTime(conversation.last_message_at)}`));
      }
      await reloadLog();
    }
    async function reloadLog() {
      clear(logBox);
      const current = conversations.find((c) => c.id === state.conversationId);
      if (!current) return;
      header.textContent = `${current.channel} conversation ${current.id} · started ${fullTime(current.created_at)}`;
      let messages;
      try {
        messages = (await api.getCompanionMessages(current.id, limit)).messages;
      } catch (error) { reportError(error, "history load failed"); return; }
      if (messages.length >= limit) {
        logBox.append(h("button", { class: "button button-quiet", type: "button", "data-testid": "load-older", onclick: () => {
          limit = Math.min(limit * 2, 2000); reloadLog();
        } }, "Show older messages"));
      }
      if (!messages.length) logBox.append(h("p", { class: "muted" }, "No messages in this conversation."));
      for (const message of messages) {
        logBox.append(h("p", { class: message.role === "user" ? "chat-user" : "chat-assistant", "data-role": message.role },
          h("span", { class: "muted small" }, `${fullTime(message.created_at)} `),
          h("strong", null, `${message.role}: `), message.content));
      }
    }
    async function send() {
      const text = input.value.trim();
      if (!text) return;
      const current = conversations.find((c) => c.id === state.conversationId);
      // The console speaks on the local channel only; replies to other channels stay with their adapters.
      const target = current && current.channel === "local" ? current.id : null;
      input.value = "";
      try {
        const reply = await api.companionChat(user.user_id, text, target);
        state.conversationId = reply.conversation_id;
        await reloadList();
        if (reply.facts_learned > 0) toast(`Learned ${reply.facts_learned} new fact(s).`, "info");
      } catch (error) { reportError(error, "chat failed"); }
    }
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") send(); });
    card.append(listBox, header, logBox,
      h("p", { class: "muted small" }, "Messages you send here go on the local channel. A WhatsApp, iMessage or webhook conversation opens as history only."),
      h("div", { class: "row" }, input, h("button", { class: "button", type: "button", onclick: send }, "Send")));
    reloadList();
    return card;
  }

  function checkinsCard(user) {
    const prefs = user.checkins;
    const card = h("section", { class: "card" }, h("h2", null, "Proactive check-ins"));
    const enabled = h("input", { type: "checkbox", checked: prefs.enabled });
    const cadence = h("input", { class: "input", type: "number", min: 15, max: 10080, value: prefs.cadence_minutes });
    const quietStart = h("input", { class: "input", placeholder: "HH:MM", value: prefs.quiet_hours ? prefs.quiet_hours.start : "" });
    const quietEnd = h("input", { class: "input", placeholder: "HH:MM", value: prefs.quiet_hours ? prefs.quiet_hours.end : "" });
    const channel = h("select", { class: "input" },
      ...["local", "webhook", "whatsapp", "imessage"].map((value) =>
        h("option", { value, selected: value === prefs.channel }, value)));
    const address = h("input", { class: "input", placeholder: "delivery address (webhook URL / phone); empty = local conversation", value: prefs.address || "" });
    const listBox = h("div", { "data-testid": "checkin-queue" });
    const statusFilter = h("select", { class: "input", name: "checkin-status", onchange: () => reloadCheckins() },
      ...["", "queued", "running", "done", "failed", "cancelled"].map((value) =>
        h("option", { value }, value || "all statuses")));
    async function reloadCheckins() {
      clear(listBox);
      try {
        const rows = (await api.listCompanionCheckins(user.user_id, statusFilter.value || null)).checkins;
        const counts = {};
        for (const row of rows) counts[row.status] = (counts[row.status] || 0) + 1;
        listBox.append(h("p", { class: "muted small", "data-testid": "checkin-counts" },
          rows.length ? Object.entries(counts).map(([k, v]) => `${k}: ${v}`).join(" · ") : ""));
        if (!rows.length) { listBox.append(h("p", { class: "muted" }, "No check-ins recorded.")); return; }
        const table = h("table", { class: "table" },
          h("tr", null, h("th", null, "due"), h("th", null, "status"), h("th", null, "channel"), h("th", null, "detail")));
        for (const row of rows) {
          table.append(h("tr", null,
            h("td", null, fullTime(row.due_at)),
            h("td", null, badge(row.status, row.status === "done" ? "ok" : row.status === "failed" ? "bad" : "muted")),
            h("td", null, row.channel),
            h("td", { class: "muted small" }, row.last_error || (row.message ? row.message.slice(0, 60) : "-"))));
        }
        listBox.append(table);
      } catch (error) { reportError(error, "check-in listing failed"); }
    }
    card.append(
      h("label", { class: "check" }, enabled, " enabled"),
      field("Cadence (minutes)", cadence),
      h("div", { class: "row" }, field("Quiet from", quietStart), field("Quiet until", quietEnd)),
      field("Channel", channel), field("Address", address),
      h("div", { class: "row" },
        h("button", { class: "button", type: "button", onclick: async () => {
          const minutes = Number(cadence.value);
          if (!Number.isInteger(minutes) || minutes < 15 || minutes > 10080) {
            toast("Cadence must be 15-10080 minutes.", "warn"); return;
          }
          if ((quietStart.value.trim() === "") !== (quietEnd.value.trim() === "")) {
            toast("Quiet hours need both a start and an end.", "warn"); return;
          }
          if (!window.confirm(`Save check-in settings for ${user.user_id}? Disabling cancels pending check-ins.`)) return;
          const body = {
            enabled: enabled.checked,
            cadence_minutes: minutes,
            quiet_hours: quietStart.value.trim() ? { start: quietStart.value.trim(), end: quietEnd.value.trim() } : null,
            channel: channel.value,
            address: address.value.trim() || null,
          };
          try {
            const result = await api.updateCompanionCheckins(user.user_id, body);
            toast(`Saved. Cancelled pending: ${result.cancelled_pending}.`, "ok");
            reloadCheckins();
          } catch (error) { reportError(error, "check-in save failed"); }
        } }, "Save check-ins"),
        h("button", { class: "button button-quiet", type: "button", onclick: async () => {
          try {
            const planned = await api.planCompanionCheckin(user.user_id);
            toast(`Next check-in queued for ${fullTime(planned.due_at)}.`, "ok");
            reloadCheckins();
          } catch (error) { reportError(error, "planning failed"); }
        } }, "Plan next now"),
        h("button", { class: "button button-quiet", type: "button", "data-testid": "deliver-due", onclick: async () => {
          if (!window.confirm("Deliver every due check-in for all users now? This needs the admin scope and sends on each user's configured channel.")) return;
          try {
            const result = await api.tickCompanionCheckins();
            const sent = result.deliveries.filter((d) => d.delivered).length;
            toast(`Planned ${result.planned}; processed ${result.deliveries.length} due check-in(s), ${sent} delivered.`, "ok");
            reloadCheckins();
          } catch (error) { reportError(error, "delivery run failed (admin scope required)"); }
        } }, "Deliver due now")),
      h("h3", null, "Queue"),
      field("Filter", statusFilter),
      listBox);
    reloadCheckins();
    return card;
  }

  root.append(usersCard, detail);
  await loadUsers();
}
