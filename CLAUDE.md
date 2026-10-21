# Checagem ao vivo: Lula × Flávio no debate da Globo

Página estática em `docs/globo/` (GitHub Pages, servida de `/docs` na `main`); pipeline em `scripts/`, prompts em `prompts/`, roteiros da noite em `roteiros/`, atalhos em `iniciar/`. Ver `README.md`.

O publicador faz commit e push de `docs/globo/data/painel.json` na `main` durante o debate: não reescrever o histórico da `main` e, numa noite ao vivo, não dar push de código sem combinar.

## Agent skills

### Issue tracker

Issues e specs ficam como markdown local em `.scratch/<feature>/`. See `agents/issue-tracker.md`.

### Triage labels

Os cinco rótulos padrão (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), como linha `Status:` em cada arquivo. See `agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` na raiz e ADRs em `agents/adr/` (fora de `docs/`, que é publicado). See `agents/domain.md`.
