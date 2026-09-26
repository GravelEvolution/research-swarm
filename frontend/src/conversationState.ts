import type { Message } from './taskTypes';

export function preparationReply(messages: Message[]): Message | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === 'user') return undefined;
    if (message.role === 'assistant' && message.kind === 'requirements') return message;
  }
  return undefined;
}

export function isModelReady(taskReady: boolean | undefined, settingsReady: unknown): boolean {
  return taskReady ?? settingsReady === true;
}
