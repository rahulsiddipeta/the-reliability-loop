// Single source of truth for topic pages, links and descriptions.
// `name` must match the `category` enum in content.config.ts.
export const topics = [
  {
    id: 'sre',
    name: 'SRE',
    number: '01',
    description:
      'Incident response, SLOs, failure management, operational maturity and the engineering practices behind reliable production systems.',
  },
  {
    id: 'platform-engineering',
    name: 'Platform Engineering',
    number: '02',
    description:
      'Internal platforms, developer experience, infrastructure abstractions and the systems that help engineering teams move safely at scale.',
  },
  {
    id: 'distributed-systems',
    name: 'Distributed Systems',
    number: '03',
    description:
      'Failure modes, resilience, scaling behavior and the architectural tradeoffs that appear when systems span machines, regions and services.',
  },
  {
    id: 'observability',
    name: 'Observability',
    number: '04',
    description:
      'Logs, metrics, traces, telemetry design and the context engineers — and increasingly AI systems — need to understand production.',
  },
  {
    id: 'ai-and-automation',
    name: 'AI & Automation',
    number: '05',
    description:
      'How automation evolves toward autonomous operations while preserving confidence, verification, blast-radius controls and human judgment.',
  },
] as const;

export type Topic = (typeof topics)[number];

export const topicUrl = (category: string) =>
  `/topics/${topics.find((topic) => topic.name === category)?.id ?? ''}`;
