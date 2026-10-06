import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

const articles = defineCollection({
  loader: glob({
    base: './src/content/articles',
    pattern: '**/*.{md,mdx}',
  }),

  schema: z.object({
    title: z.string(),
    description: z.string(),
    pubDate: z.coerce.date(),
    updatedDate: z.coerce.date().optional(),

    author: z.string().default('Rahul Siddipeta'),

    category: z.enum([
      'SRE',
      'Platform Engineering',
      'Distributed Systems',
      'Observability',
      'AI & Automation',
    ]),

    tags: z.array(z.string()).default([]),

    readTime: z.string(),

    featured: z.boolean().default(false),

    draft: z.boolean().default(false),
  }),
});

export const collections = { articles };