# Como adicionar um projeto

1. Crie `site/projetos/<nome>/index.html`. O jeito mais rápido é copiar `projetos/poder-de-compra/` e trocar o JSON que ele lê.
2. Gere o JSON do projeto em `site/data/<nome>.json` (pipeline próprio em `pipeline/` ou um notebook exportado).
3. Adicione um cartão em `site/index.html`, na seção *Projetos* (troque o cartão "Em breve").
4. Prints: salve em `site/assets/prints/` e registre em `prints.json` com `"projeto": "<nome>"`.
5. Documentação: crie `site/docs/<nome>.md` e adicione na lista `DOCS` de `site/docs.html`.

Depois é só publicar: o site é estático (HTML, CSS, JS e JSON).
