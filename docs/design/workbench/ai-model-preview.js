import { openAIChannelSettings as openBaseSettings } from "/js/core/ai-channel-settings.js?base";

export function openAIChannelSettings(ctx, onSaved, trigger) {
  openBaseSettings(ctx, onSaved, trigger, { demo: true });
}
