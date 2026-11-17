const models = {
  openai: { id: "gpt-6-luna", name: "GPT-6 Luna" },
  deepseek: { id: "deepseek-flash", name: "DeepSeek V4.1 Flash" },
};

export function openAIChannelSettings(ctx, onSaved, trigger, { demo = false } = {}) {
  if (document.querySelector("[data-ai-settings]")) return;
  const saved = ctx.health?.ai_channel || {};
  const dialog = document.createElement("dialog");
  dialog.className = "import-sheet bridge-sheet api-settings";
  dialog.dataset.aiSettings = "";
  dialog.setAttribute("aria-labelledby", "api-settings-title");
  dialog.innerHTML = `<h2 id="api-settings-title">API 直连设置</h2>
    <p>选好服务商即可。文字分析和衣物照片识别用的是同一个模型。</p>
    <form>
      <label>API 服务商<select name="provider"><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option></select></label>
      <label>API Key<input name="key" type="password" maxlength="512" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
      <label>模型<select name="model" required></select></label>
      <input type="hidden" name="vision_model">
      <p class="faint">两个服务商都只有一档高能力模型，思考强度都按高，文字分析和照片识别共用它。生成平铺图和添置款式图仍需 WorkBuddy。</p>
      <p data-result role="status"></p>
      <button type="submit" class="primary">保存并使用 API 直连</button>
      <button type="button" data-close>关闭</button>
    </form>`;
  document.body.append(dialog);
  const form = dialog.querySelector("form");
  const fields = form.elements;
  const result = dialog.querySelector("[data-result]");
  fields.provider.value = Object.hasOwn(models, saved.provider) ? saved.provider : "openai";
  fields.key.readOnly = demo;
  function populate() {
    const provider = fields.provider.value;
    const keepKey = saved.configured && saved.provider === provider;
    const model = models[provider];
    fields.key.value = "";
    fields.key.required = !demo && !keepKey;
    fields.key.placeholder = demo ? "工作台演示，无需填写"
      : keepKey ? "留空保留已保存的 Key" : "粘贴自己的 API Key";
    fields.model.replaceChildren(new Option(model.name, model.id));
    // 一个模型管文字和图片两用。vision_model 仍要提交：后端 profile() 要求这个键存在，
    // 打标走视觉通道时读的也是它（app/services/llm/client.py），所以用隐藏字段跟着选中项走，
    // 不要因为界面上看不见就把它删掉。
    fields.vision_model.value = model.id;
  }
  populate();
  fields.provider.addEventListener("change", populate);
  let busy = false;
  dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  dialog.addEventListener("close", () => {
    fields.key.value = "";
    dialog.remove();
    if (trigger?.isConnected) {
      if (trigger.closest("details")) trigger.closest("details").open = true;
      trigger.focus();
    } else {
      document.querySelector(".status-menu [data-ai-configure]")?.focus();
    }
  });
  form.querySelector("[data-close]").onclick = () => dialog.close();
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    const values = Object.fromEntries(new FormData(form));
    const controls = [...form.querySelectorAll("input,select,button")];
    busy = true;
    controls.forEach(node => { node.disabled = true; });
    result.textContent = "正在保存…";
    try {
      onSaved(await ctx.api.configureAIChannel({ ...values, mode: "direct" }));
      dialog.close();
    } catch (error) {
      result.textContent = error?.message || "无法保存 API 设置。";
    } finally {
      busy = false;
      controls.forEach(node => { node.disabled = false; });
    }
  };
  dialog.showModal();
}
