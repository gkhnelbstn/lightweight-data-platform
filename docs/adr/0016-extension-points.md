# 0016 — Which extension points take plugins, and which stay code

## Context

Four things in this repository could plausibly be extended by somebody who does
not want to fork it. One of them already can be, and the difference between it
and the other three is the whole argument.

**A contract is already a plugin.** Drop a `*.odcs.yaml` into `contracts/`,
`load_contracts()` finds it, the checks run, the UI lists it. No code changes.
It is data rather than code, it is declarative, and it is the single source of
truth (invariant 1). Nothing about it needs improving, and it is the shape the
other three get measured against.

The other three are code edits today:

1. **Rule kinds.** `core/rules.py` holds a dict of
   `kind -> (builder, dimension, description, label)` and a second of
   parameters. `/api/rules/catalogue` serves it and the form draws itself from
   what comes back, so the *front end* is already plugin-shaped — only the
   registration is a commit to this repo.
2. **Source engines.** Mostly not ours: `datacontract test` owns the `servers`
   type. Ours per engine were three functions — the window, the failing-rows
   sampler, the ODDRN generator — and each was a branch on the engine name in a
   *different file*, which is how the SQL Server contract stayed cumulative for
   forty-five days without anyone noticing.
3. **Catalog integrations.** `integrations/odd/` is a directory convention, not
   a registry. A second catalog means a second directory and an import wherever
   the runner pushes.

A registry itself is cheap. `importlib.metadata.entry_points(group=...)` is
about fifteen lines and no dependency. What is not cheap is what it implies: a
public interface, a version for it, and a promise not to break it. Today a rule
builder's signature can change in one commit because every caller is in this
repository.

## Decision

**Different answers for the three, and the reason is how many implementations
exist.**

### Rule kinds: yes, through entry points

The dict was always the registry. A package declares

```toml
[project.entry-points."ldp.rules"]
iban_checksum = "my_pkg.rules:iban_checksum"
```

and the named object is a `RuleKind`, or something that returns one.
`core/rule_plugins.py` reads the group once, at import, and hands each to
`core.rules.register`.

**Only the predicate shape is published.** A plugin's builder is
`(column, params) -> exp.Expression` — the predicate that is true of a *broken*
row — and this repository wraps it into `select count(*) … where …` in the
source's own dialect. The two whole-statement kinds stay built in: a duplicate
needs a `GROUP BY` and a foreign key a join, and publishing them would publish
the dialect handling with them. This is the line that keeps the promise small.

**Everything is checked at registration**, not when somebody fills the form in:

* the kind is not already taken;
* the dimension is one `core/scoring.py` actually weights — an unknown one is
  not an error anywhere else, it silently takes `DEFAULT_WEIGHT`, so a rule
  whose weight nobody chose would count as much as a broken join;
* the menu label is not a template, because it is read before the values are
  known;
* the description formats against the parameters the kind declares;
* every parameter is a field the form can draw.

**A plugin that raises is fatal.** `load_plugins` re-raises with the entry
point's name and value. A catalogue that has silently lost a kind is the worst
outcome available: the form simply would not offer it, and nobody would know
why.

`tests/test_rules.py` covers the built-ins, which is why `BUILTIN` is frozen
before the group is read; a plugin's own kinds are its own to test.

### Source engines: refactored, and that was the fix

`core/engines/` is one module per engine — the window, the sampler, the counts,
the ODDRN vocabulary — and the branch is a lookup. That closed the actual
problem, which was the scattering rather than the registration. A registry here
would be a five-line change on top of it if a fourth engine ever arrives from
outside, and until one does it would be a public interface with no second
implementation to check it against.

### Catalog integrations: not yet

One integration exists. An interface designed against a single implementation
gets the abstraction wrong, and this is what invariant 6 is about: no new
infrastructure — or in this case no new promise — without something to justify
it. ADR 0009 makes the same argument about the UI fork. When a second catalog
is actually wanted, the mapper-plus-validation shape in `integrations/odd/` is
the thing to generalise, and it will be obvious by then which parts of it were
ODD-specific.

## Consequences

* A rule kind can be added by a package this repository has never heard of, and
  the UI needs no change — the catalogue was already the only vocabulary.
* `build()`'s two shapes are now a boundary rather than an implementation
  detail. `WHOLE_STATEMENT` is not part of the interface, and adding a
  whole-statement kind is still a commit here.
* `RuleKind.builder` returns a `sqlglot` expression, so **sqlglot is in the
  published interface**. That is the one dependency a plugin author has to
  match, and the reason to keep the surface at exactly one function.
* Registration validates more than the built-ins ever needed, which is the
  price of accepting code from outside: the built-ins are read in review, a
  plugin is not.
* Two extension points stay code edits, and this record is the answer to "why
  not those too" so that the question does not get re-litigated per pull
  request.

## On upgrade

* **A sqlglot major bump is the breaking change to watch.** The predicate type
  is the promise; if `exp.Expression` moves or its constructors change, every
  plugin breaks, and this repository cannot fix them. Consider it a major
  version of the `ldp.rules` group.
* **If a rule builder's signature has to change**, it is no longer a one-commit
  change. Either keep the old shape working or say plainly, here, that the
  group's version changed.
* **Delete this decision's rules half** if datacontract-cli grows its own
  pluggable check kinds — deriving and running checks is theirs (invariant 2),
  and a vocabulary that lives there would make ours redundant rather than
  extensible.
* **Revisit the integrations half when a second catalog exists**, not before.
  The trigger is a real second implementation, not a request for one.
* If the `ldp.rules` group is never used by anything outside this repository
  within a release or two, delete `core/rule_plugins.py` and keep the
  validation in `register` — the validation earns its place either way.
