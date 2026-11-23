# What Skein is for

> One sentence, one loop, three work surfaces. Product decisions defer to this page.

## One sentence

**Turn real user feedback into fixes a person can accept.**

Not "another agent platform", and not a ticket tracker. Skein does one thing: starting from
real feedback, it dispatches coding agents on nodes to produce a fix, and delivers the
branch back to a human.

## Who it is for

- Maintainers of a small team — typically one to three people.
- Who answer three questions every day: **what needs me now? who is doing what? where is it
  stuck?**
- Without assuming a dedicated SRE, multi-tenancy, or anyone with time to read long logs.

## The loop

```text
external feedback (post / comment / chat / a monitoring signal)
      │
      ▼
admission (ignore / record / process) — decided by code
      │
      ▼
ticket (threads merged, deduplicated)
      │
      ▼
dispatched to an isolated coding-agent session on a node
      │
      ▼
delivery (branch, commits, diffstat, self-test summary, draft PR)
      │
      ▼
human acceptance (approve / reject)
```

The human gate is hard. The system may collect, judge and execute automatically; it does
**not** merge code for anyone.

## Three work surfaces

The main navigation has three entries, answering three different questions:

| Surface | Question | Contains |
| --- | --- | --- |
| **Today** | What should I do now? | Awaiting review, stuck dispatches, what is running |
| **Tickets** | Where did this get to? | Ticket list, detail, dispatch, review, delivery card |
| **Operations** | Who is running what, where is it stuck? | Overview map, recent dispatches, nodes, worker sessions, event stream, signals |

Internal mechanics (admission policies, prompt revisions) do not get a user-facing page;
they are either data or need a more precise entry point than a page called "platform".

## What it is not

- Not a chatbot: chat is one optional intake; the main interface is tickets and state.
- Not CI/CD: it does not merge or release for you.
- Not a general multi-tenant agent platform.
- Not a dashboard: metrics exist only where they point at an action. The Today map is
  navigation — every point opens the object it represents.

## Product principles

1. **One screen answers "who is doing what, where is it stuck".**
2. **Every number opens the object behind it.**
3. **The system stays quiet; it speaks when a person needs to act.**
4. **Human review is the hard gate; buttons state their consequence.**
5. **An empty state says what to do next, not just "no data".**
6. **Failures are visible**: failed dispatches and failed PRs say why, on screen and in the
   audit trail.
7. **Zero configuration works**: with nothing configured, the core loop still runs.

## What success looks like

- Time from feedback arriving to a human accepting the fix.
- How long accepted-for-review items wait.
- First-pass acceptance rate.
- How many things get stuck (dispatch timeouts, offline nodes).
- How many times a day a person has to intervene.
