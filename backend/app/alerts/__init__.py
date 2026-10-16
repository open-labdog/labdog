"""What LabDog does with an alert besides investigating it.

Recording alerts lives in :mod:`app.ai.alerts` for history's sake; this
package holds what needs no AI: reading what an alert's labels ask for
(:mod:`app.alerts.labels`), the actions alerts may run and the record of
what each alert's request came to (:mod:`app.alerts.models`).
"""
