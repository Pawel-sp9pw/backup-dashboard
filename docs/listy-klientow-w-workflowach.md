# Listy klientów w workflowach DB2

Zalecany model:

- `DB2_LINUX_CLIENTS` — klienci, których sprawdza Linuxowy `db2ckbkp`.
- `DB2_WINDOWS_CLIENTS` — klienci, których sprawdza Windowsowy `db2ckbkp.exe`.
- `OTHER_SYSTEM_CLIENTS` — klienci z innym systemem / kopią nie-DB2, których nie sprawdza żaden checker DB2.

Dla aktualnego środowiska:

```text
DB2_LINUX_CLIENTS="vena"
DB2_WINDOWS_CLIENTS="bes etos galena iwaniuk novo-med-klobuck novo-med-miedzno novo-med-panki novo-med-popow nowinski pulsmed salomon"
OTHER_SYSTEM_CLIENTS="jagielska kulej"
```

`kulej` jest innym systemem, więc powinien trafiać jako `NOT_APPLICABLE`, a nie `ERROR` ani `SKIPPED_NOT_DB2`.

Dashboard rozpoznaje status `NOT_APPLICABLE` jako neutralny.
