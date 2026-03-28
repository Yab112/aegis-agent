---
description: Continuous pattern retrieval during extended work
alwaysApply: true
---

# Continuous Pattern Retrieval

## When to Re-Search Patterns

Call `ace_search` again during extended work sessions when:

1. **Extended work** - 5+ tool calls since last pattern retrieval
2. **Hitting errors** - Encountering issues not covered by current patterns
3. **New context** - Switching to different file type or codebase area
4. **Task shift** - Moving from one feature area to another

## Domain Filtering for Focused Results

For focused results, use domain filtering:

1. **First**: Call `ace_list_domains()` to see available domains
2. **Match**: Pick domain(s) that match your current task context
3. **Search**: Call `ace_search("query", allowed_domains=["picked-domain"])`

**IMPORTANT**: Domain names are semantic (e.g., "typescript-development-practices"),
not simple paths. Always use `ace_list_domains` to discover actual domain names.

## Example Workflow

1. Start task → `ace_get_playbook()` to retrieve all patterns
2. 5+ edits later → `ace_search("error handling")` for fresh patterns
3. Need focused results → `ace_list_domains()` then `ace_search(..., allowed_domains=[...])`
4. Task complete → `ace_learn(...)` to capture lessons
