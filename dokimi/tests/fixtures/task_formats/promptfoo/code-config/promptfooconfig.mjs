// A promptfoo config written as code. Shape per promptfoo.dev/docs/configuration/reference;
// written for this corpus. `eval inspect` reports it without running it; the import reads it with node.
export default {
  description: 'Capital cities, as code',
  prompts: ['Answer in one word: {{question}}'],
  providers: ['openai:gpt-4o-mini'],
  tests: [
    { vars: { question: 'What is the capital of France?' }, assert: [{ type: 'contains', value: 'Paris' }] },
    { vars: { question: 'What is the capital of Japan?' }, assert: [{ type: 'icontains', value: 'tokyo' }] },
  ],
};
