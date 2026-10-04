# Key classification to the function, not to its name

Review session one found that a classification could be attached to one function and
silently inherited by another (F-005), and that two common ways of packaging a tool crashed
instead of being judged (F-006).

## The problem

The registry was keyed by `module:qualname` — the function's import path and name. The
original reasoning, in the `decorators.py` docstring, was that this beats a marker attribute
on the function, because an attribute can be hand-set.

That argument is circular, and a cold reviewer took it apart: `__qualname__` and `__module__`
are themselves plain writable attributes. So the same spoof works one level down. Worse,
because registration deliberately does not wrap the function, nothing tied a registration to
the code that actually runs. Three ways through:

```python
# 1. classify a stub, then rebind the name
@regulated_tool(FinancialCategory.PSD2_PAYMENT_EXECUTION)
def pay(): return "audited stub"
pay = _real_transfer          # never classified, same name

# 2. borrow a name
evil.__qualname__ = "pay"

# 3. two closures from one factory share a name
stub, evil = factory("harmless"), factory("drain the account")
```

All three produced an agent that reported itself fully classified.

## The fix

Key the registry by the **function object itself**. Functions are hashable, so this is a
small change:

```python
TOOL_REGISTRY: dict[Callable[..., Any], RegisteredTool]
```

All three vectors close, because in each case the object handed to the agent is not the
object that was decorated. The closure case closes too — which neither of the two
alternatives I first considered would have managed, since closures from one factory share a
code object.

It also fixes the crash: `functools.partial(classified_tool)` and callable objects are
hashable, so they are now *judged* rather than raising `AttributeError` on a missing
`__qualname__`. A `partial` of a classified tool correctly reports as unclassified, because
it genuinely is a different callable.

## Why not wrap the function

Wrapping would make identity travel with the object, which is the textbook answer. It was
rejected on Day 1 and is still rejected: wrapping changes signatures, hides docstrings, and
breaks introspection, and there is a test pinning that a decorated function **is** the
function you wrote. Identity keying gets the same protection without touching the object.

## Two costs, stated rather than hidden

- **Bound methods need unwrapping.** `obj.method` creates a fresh object on each access, so
  it would never match the function decorated in the class body. The lookup now resolves
  `__func__` explicitly, which means one classification is shared across all instances of a
  class — the same behaviour as before, now by intention rather than accident.
- **The registry holds strong references** to decorated functions. Harmless in practice,
  since they are module globals that live as long as the process, but it is a change.

## Verifying

`python3 tools/run_mutations.py A8 A9 A10` — all currently uncaught. The Day 1 stacking and
identity-preservation tests must stay green; they are the constraint this fix works within.
