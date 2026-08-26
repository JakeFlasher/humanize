# Research foundations and design implications

RLCR v2 uses research as design evidence, not as a claim that an automated
review proves correctness. The papers below motivated specific controls.

| Work | Relevant result | RLCR v2 response |
| --- | --- | --- |
| [Self-Refine: Iterative Refinement with Self-Feedback](https://arxiv.org/abs/2303.17651) | Iterative feedback and refinement can improve first-pass model output without retraining. | Persist structured correction packets and iterate over clean committed artifacts. |
| [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366) | Linguistic feedback plus episodic memory can improve later trials. | Preserve findings, fingerprints, packets, and event history rather than relying on transient conversation memory. |
| [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798) | Intrinsic self-correction can fail or degrade without external feedback. | Require fresh reviewer contexts and support external deterministic test evidence; never accept an implementer's self-assessment. |
| [Improving Factuality and Reasoning through Multiagent Debate](https://arxiv.org/abs/2305.14325) | Multiple model instances can improve some reasoning and factuality tasks. | Fan out into independent specification and correctness lanes, but use controller consensus instead of unconstrained debate. |
| [Replacing Judges with Juries](https://arxiv.org/abs/2404.18796) | Panels can reduce some single-judge and intramodel evaluation weaknesses, especially when model families are diverse. | Require two exact lane verdicts and disclose that same-family reviewers are not genuinely diverse jurors. |
| [Large Language Models are Inconsistent and Biased Evaluators](https://arxiv.org/abs/2405.01724) | Model evaluators can show familiarity, anchoring, distribution, and prompt sensitivity. | Use role-specific prompts, strict JSON, evidence-bearing findings, deterministic semantic checks, and no scalar quality score. |
| [SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering](https://arxiv.org/abs/2405.15793) | The agent-computer interface materially affects software-agent performance. | Give reviewers stable named artifacts, explicit boundaries, bounded outputs, and a minimal read-only CLI environment. |
| [LLM4TDD](https://arxiv.org/abs/2312.04687) | Test-driven feedback is a useful iterative signal for generated programs. | Add named, same-commit, digest-verified evidence records and make required checks controller-enforced. |

## Practical conclusions

1. Iteration needs an external stopping rule. RLCR stops only on exact
   controller consensus or a hard budget/terminal state.
2. Feedback needs provenance. Every decisive review is tied to start commit,
   candidate commit, plan, contract, configuration, evidence, and patch
   digests.
3. Independence is graded, not binary. Fresh processes avoid shared context,
   while the common model family remains a correlated-failure risk.
4. Model review cannot replace executable validation. Required checks must pass
   at the exact candidate commit and remain intact through review.
5. More agents are not automatically better. Two orthogonal lanes are the
   smallest useful panel for this plugin; retries are per-lane and budgeted.
