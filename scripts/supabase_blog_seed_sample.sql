-- Sample blog posts for yabibal.site — run in Supabase → SQL Editor after
-- scripts/supabase_blog_posts.sql has been applied.
--
-- Safe to re-run: uses ON CONFLICT (slug) DO NOTHING.

-- ─── 1. Career pivot: Python, AI, security, orchestration ───────────────────
insert into public.blog_posts (
  slug,
  title,
  description,
  body_md,
  status,
  tags,
  topic_key,
  published_at
)
values (
  'pivoting-to-python-ai-security-and-orchestration',
  'Pivoting fully into Python and AI: security, orchestration, and the messy middle',
  'Why I am doubling down on Python and AI, what I am studying in AI security and orchestration, and the resources that are actually helping.',
  $md$
## The shift

I am moving my career path toward **Python and AI** in a deliberate way—not as a buzzword, but as the stack where I build, ship, and break things safely. That means writing more services in Python, leaning on modern LLM tooling, and treating **security** and **orchestration** as first-class skills, not afterthoughts.

## What I am studying now

### AI security

Models and agents are software. They need threat modeling like anything else. I have been digging into the [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/) as a practical checklist: prompt injection, insecure output handling, supply chain issues for models and data, and excessive agency when you give tools to an agent.

For a broader governance lens, [NIST’s AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework) is a solid frame for thinking about trustworthiness and deployment risk—not bureaucracy for its own sake, but vocabulary you can use with teams and clients.

### Orchestration

“Orchestration” here means **multi-step workflows**: routing, tools, memory, retries, and human handoffs. I spend time with [LangGraph](https://langchain-ai.github.io/langgraph/) concepts (graphs, state, checkpoints) because they map cleanly to real products—not just one-shot chat completions.

If you prefer a higher-level mental model, [LangChain’s overview](https://python.langchain.com/docs/introduction/) still helps for chains, tools, and RAG patterns, even when you end up on a slimmer stack.

### Python as the spine

None of the above sticks without a comfortable home language. [Python.org’s tutorial](https://docs.python.org/3/tutorial/) is the boring answer that works; for APIs I reach for [FastAPI](https://fastapi.tiangolo.com/) because typed models and OpenAPI out of the box match how I like to ship.

## What I actually do day to day

A lot of it is glue and judgment: wiring embeddings and retrieval, hardening prompts, logging and evals, and knowing when **not** to automate. Security and orchestration are the difference between a demo and something you can run in production without losing sleep.

## Links quick list

| Topic | Link |
|--------|------|
| OWASP LLM Top 10 | https://owasp.org/www-project-top-10-for-large-language-model-applications/ |
| NIST AI RMF | https://www.nist.gov/itl/ai-risk-management-framework |
| LangGraph | https://langchain-ai.github.io/langgraph/ |
| LangChain docs | https://python.langchain.com/docs/introduction/ |
| Python tutorial | https://docs.python.org/3/tutorial/ |
| FastAPI | https://fastapi.tiangolo.com/ |

If any of this resonates and you are on a similar pivot, feel free to reach out through [my site](https://yabibal.site)—always happy to compare notes.
$md$,
  'published',
  array['career', 'python', 'ai', 'security', 'orchestration']::text[],
  'sample_career_python_ai_pivot',
  now() - interval '3 days'
)
on conflict (slug) do nothing;

-- ─── 2. Human reinforcement learning, work on Turing ─────────────────────────
insert into public.blog_posts (
  slug,
  title,
  description,
  body_md,
  status,
  tags,
  topic_key,
  published_at
)
values (
  'human-feedback-rl-and-turing',
  'Human-in-the-loop, reinforcement learning from feedback, and building on Turing',
  'Notes on RLHF-style loops, why human judgment still matters, and how my work on Turing fits into that picture—with links to read alongside.',
  $md$
## Humans in the loop are not a bug

A lot of “AI” headlines pretend the model is autonomous. In practice, **human feedback**—ranking outputs, correcting tone, catching hallucinations—is what makes systems usable. That pattern sits under ideas like **RLHF** (reinforcement learning from human feedback): reward signals come from people (or from models trained to mimic people), not only from static loss on a dataset.

A readable on-ramp is Hugging Face’s [Illustrated RLHF](https://huggingface.co/blog/rlhf), which walks through preference data, reward modeling, and policy tuning without requiring a PhD to get value from the diagram.

For the research lineage, OpenAI’s [Learning to summarize with human feedback](https://arxiv.org/abs/2009.01325) is one of the papers that popularized the modern recipe; Anthropic’s writing on [Constitutional AI](https://www.anthropic.com/news/constitutional-ai-harmlessness-from-ai-feedback) explores related ideas where AI feedback augments human oversight—still contested, still evolving.

## What I mean by reinforcement learning here

I am not claiming a neat lab setup on every task. In product work, “RL” often shows up as **iterate from evals**: ship, measure, label failures, change prompts or tools, repeat. DeepMind’s [scalable RL in complex environments](https://deepmind.google/research/) is the research-heavy end of the spectrum; your dashboard and error taxonomy are the day-job version of the same instinct.

## Turing

I am currently working in the **Turing** ecosystem—[Turing.com](https://www.turing.com/) connects engineers with serious remote roles and, in my case, intersects with work where **human judgment** and **model behavior** have to align (think evaluation, refinement, and the kind of feedback loops that make deployed AI less brittle).

If you are exploring similar work, their [developer-focused pages](https://www.turing.com/) are the canonical entry point; compare that with general RLHF reading above and you start to see the same theme: **models improve when human intent is explicit in the loop**.

## Links quick list

| Topic | Link |
|--------|------|
| Illustrated RLHF (Hugging Face) | https://huggingface.co/blog/rlhf |
| OpenAI: Learning to summarize with human feedback | https://arxiv.org/abs/2009.01325 |
| Anthropic: Constitutional AI | https://www.anthropic.com/news/constitutional-ai-harmlessness-from-ai-feedback |
| DeepMind research (RL / agents) | https://deepmind.google/research/ |
| Turing | https://www.turing.com/ |

## Closing

If your work touches **human reinforcement signals**, **orchestrated agents**, or **evaluation at scale**, we are probably solving adjacent puzzles. More on the rest of my stack and projects on [yabibal.site](https://yabibal.site).
$md$,
  'published',
  array['rlhf', 'reinforcement-learning', 'human-feedback', 'turing', 'ai']::text[],
  'sample_turing_rlhf_human_loop',
  now() - interval '1 day'
)
on conflict (slug) do nothing;
