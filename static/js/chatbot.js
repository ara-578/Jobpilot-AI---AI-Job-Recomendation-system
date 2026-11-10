document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-chatbot-root]");
  if (!root) return;

  const panel = root.querySelector("[data-chatbot-panel]");
  const launcher = root.querySelector("[data-chatbot-open]");
  const closeButton = root.querySelector("[data-chatbot-close]");
  const clearButton = root.querySelector("[data-chatbot-clear]");
  const messages = root.querySelector("[data-chatbot-messages]");
  const form = root.querySelector("[data-chatbot-form]");
  const input = root.querySelector("[data-chatbot-input]");
  const sendButton = root.querySelector("[data-chatbot-send]");
  const suggestions = root.querySelector("[data-chatbot-suggestions]");
  const csrfToken = form.querySelector("[name=csrfmiddlewaretoken]").value;
  let isSubmitting = false;

  const timestamp = value => new Intl.DateTimeFormat([], {
    hour: "numeric",
    minute: "2-digit",
  }).format(value ? new Date(value) : new Date());

  const scrollToBottom = () => {
    messages.scrollTop = messages.scrollHeight;
  };

  const makeCard = data => {
    const card = document.createElement("article");
    card.className = "jobpilot-chat__card";
    const title = document.createElement("strong");
    title.textContent = data.title || data.name || data.job_title || "Result";
    card.append(title);
    const details = [];
    if (data.company) details.push(data.company);
    if (data.location) details.push(data.location);
    if (data.salary) details.push(data.salary);
    if (data.status) details.push(`Status: ${data.status}`);
    if (data.applied_at) details.push(`Applied ${data.applied_at}`);
    if (data.starts_at) details.push(`Interview ${timestamp(data.starts_at)} (${data.mode || "scheduled"})`);
    if (data.applicant_count !== undefined) details.push(`${data.applicant_count} applicants`);
    if (data.match_score !== null && data.match_score !== undefined) {
      details.push(`${data.match_score}% match`);
    }
    if (data.matching_skills?.length) details.push(`Skills: ${data.matching_skills.join(", ")}`);
    const detail = document.createElement("p");
    detail.textContent = details.join(" · ");
    card.append(detail);
    if (data.url) {
      const link = document.createElement("a");
      link.href = data.url;
      link.textContent = "View job details";
      card.append(link);
    }
    if (data.interviews?.length) {
      const interview = document.createElement("p");
      interview.textContent = data.interviews.map(item =>
        `Interview: ${timestamp(item.starts_at)} (${item.mode})`
      ).join(" · ");
      card.append(interview);
    }
    return card;
  };

  const renderMessage = (sender, content, metadata = {}, createdAt = null) => {
    const article = document.createElement("article");
    article.className = `jobpilot-chat__message jobpilot-chat__message--${sender}`;
    if (sender === "assistant") {
      const avatar = document.createElement("span");
      avatar.className = "jobpilot-chat__message-avatar";
      avatar.setAttribute("aria-hidden", "true");
      const icon = document.createElement("i");
      icon.className = "fa-solid fa-wand-magic-sparkles";
      avatar.append(icon);
      article.append(avatar);
    }

    const wrapper = document.createElement("div");
    wrapper.className = "jobpilot-chat__message-content";
    const paragraph = document.createElement("p");
    paragraph.textContent = content;
    wrapper.append(paragraph);
    const time = document.createElement("time");
    time.dateTime = createdAt || new Date().toISOString();
    time.textContent = timestamp(createdAt);
    wrapper.append(time);

    const cards = [];
    ["jobs", "applications", "applicants", "candidates", "interviews"].forEach(key => {
      if (Array.isArray(metadata[key])) cards.push(...metadata[key]);
    });
    if (cards.length) {
      const cardList = document.createElement("div");
      cardList.className = "jobpilot-chat__cards";
      cards.forEach(item => cardList.append(makeCard(item)));
      wrapper.append(cardList);
    }
    article.append(wrapper);
    messages.append(article);
    scrollToBottom();
    return article;
  };

  const showTyping = () => {
    const article = document.createElement("article");
    article.className = "jobpilot-chat__message jobpilot-chat__message--assistant";
    article.dataset.typing = "true";
    const avatar = document.createElement("span");
    avatar.className = "jobpilot-chat__message-avatar";
    avatar.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles" aria-hidden="true"></i>';
    const bubble = document.createElement("span");
    bubble.className = "jobpilot-chat__typing";
    bubble.setAttribute("aria-label", "Assistant is typing");
    bubble.innerHTML = "<span></span><span></span><span></span>";
    article.append(avatar, bubble);
    messages.append(article);
    scrollToBottom();
    return article;
  };

  const setOpen = open => {
    panel.hidden = !open;
    panel.setAttribute("aria-hidden", String(!open));
    launcher.setAttribute("aria-expanded", String(open));
    if (open) input.focus();
  };

  const syncTextarea = () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  };

  const submitMessage = async text => {
    const message = String(text || "").trim();
    if (!message || isSubmitting) {
      input.focus();
      return;
    }
    isSubmitting = true;
    sendButton.disabled = true;
    renderMessage("user", message);
    input.value = "";
    syncTextarea();
    const typing = showTyping();
    try {
      const response = await fetch(root.dataset.messageUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
          "Accept": "application/json",
        },
        body: JSON.stringify({ message }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "The message could not be sent.");
      typing.remove();
      renderMessage("assistant", result.reply, result);
      suggestions.hidden = false;
      if (Array.isArray(result.follow_up) && result.follow_up.length) {
        suggestions.replaceChildren();
        result.follow_up.forEach(question => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "jobpilot-chat__suggestion";
          button.dataset.chatbotSuggestion = question;
          button.textContent = question;
          suggestions.append(button);
        });
      }
    } catch (error) {
      typing.remove();
      renderMessage("assistant", error.message || "The assistant is temporarily unavailable.");
    } finally {
      isSubmitting = false;
      sendButton.disabled = false;
      input.focus();
    }
  };

  const loadHistory = async () => {
    try {
      const response = await fetch(root.dataset.historyUrl, {
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
      });
      if (!response.ok) return;
      const data = await response.json();
      if (!data.messages?.length) return;
      messages.replaceChildren();
      data.messages.forEach(item => renderMessage(
        item.sender, item.content, item.metadata || {}, item.created_at
      ));
    } catch {
      // History is optional; the current chat remains usable if it cannot load.
    }
  };

  input.addEventListener("input", syncTextarea);
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitMessage(input.value);
    }
  });
  launcher.addEventListener("click", () => setOpen(panel.hidden));
  closeButton.addEventListener("click", () => setOpen(false));
  form.addEventListener("submit", event => {
    event.preventDefault();
    submitMessage(input.value);
  });
  messages.addEventListener("click", event => {
    const link = event.target.closest("a");
    if (link && link.origin !== window.location.origin) {
      event.preventDefault();
    }
  });
  suggestions.addEventListener("click", event => {
    const button = event.target.closest("[data-chatbot-suggestion]");
    if (button) submitMessage(button.dataset.chatbotSuggestion);
  });
  clearButton.addEventListener("click", async () => {
    const hasConversation = messages.querySelectorAll(".jobpilot-chat__message:not([data-welcome-message])").length > 0;
    if (!hasConversation) {
      return;
    }
    if (!window.confirm("Clear this conversation?")) {
      return;
    }
    clearButton.disabled = true;
    try {
      const response = await fetch(root.dataset.clearUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": csrfToken,
          "Accept": "application/json",
        },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not clear the conversation.");
      messages.replaceChildren();
      renderMessage(
        "assistant",
        "Hello! Welcome to JobPilot AI Assistant 👋\nI'm here to help you find suitable jobs, understand your resume, improve your ATS score, track your applications and explore JobPilot features.\nHow can I help you today?"
      );
    } catch (error) {
      renderMessage("assistant", error.message || "Could not clear the conversation.");
    } finally {
      clearButton.disabled = false;
    }
  });

  const welcomeTime = root.querySelector("[data-chatbot-welcome-time]");
  if (welcomeTime) welcomeTime.textContent = timestamp();
  syncTextarea();
  loadHistory();
});
