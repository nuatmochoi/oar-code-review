"""Identical semantic anchor reminder for both controlled representations."""
ANCHOR_REASONING=('Choose the operation whose actual behavior or changed use causes the reported failure; '
    'a nearby guard, docstring, or unrelated call is not a substitute. '
    'Explain the exact trigger, failing operation, and observable impact. '
    'For a failure inside another function, select the relevant changed HEAD calling/using operation as the report anchor '
    'and support the callee path with evidence. ')
