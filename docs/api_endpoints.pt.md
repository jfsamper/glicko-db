# Rotas e notas de integração

Consulte a [Ajuda da interface](user_interface.pt.md) para instruções orientadas a tarefas e o [README em português](../README.pt.md) para instalação e configuração.

## Índice

- [Estado da API](#estado-da-api)
- [Rotas GET públicas](#rotas-get-públicas)
- [Visibilidade e ratings dos torneios](#visibilidade-e-ratings-dos-torneios)
- [Autenticação e rotas de membros](#autenticação-e-rotas-de-membros)
- [Grupos de rotas administrativas](#grupos-de-rotas-administrativas)
- [Orientações para integrações](#orientações-para-integrações)

## Estado da API

A aplicação atual é uma aplicação web Flask renderizada no servidor. **Ela não publica atualmente uma API REST pública estável** em `/api`. As rotas `/api/backup`, `/api/player`, `/api/tournament` e outras semelhantes do rascunho anterior não estão implementadas e não devem ser usadas em integrações.

As ações administrativas são envios de formulários HTML. Elas exigem uma sessão autenticada, a função adequada e um token CSRF válido. Algumas ações administrativas assíncronas retornam uma pequena resposta JSON de redirecionamento somente quando a solicitação inclui `X-Requested-With: XMLHttpRequest`; isso é um comportamento interno do navegador, não um contrato de API versionado.

## Rotas GET públicas

Estas páginas podem ser abertas sem uma conta de administrador:

| Rota | Finalidade |
| --- | --- |
| `/` | Página inicial, resumo do ranking, estatísticas e notícias publicadas |
| `/rankings` | Ranking público paginado |
| `/players` | Diretório e filtros de jogadores |
| `/player/view?id=<player_id>` | Perfil e histórico de partidas de um jogador |
| `/matches` | Lista e filtros de partidas |
| `/tournaments` | Lista de torneios públicos |
| `/tournaments/<tournament_id>` | Detalhes, emparelhamentos e classificação de um torneio |
| `/reports` | Relatórios por período e jogador |
| `/reports/export.csv` | Versão CSV dos filtros selecionados |
| `/reports/export.pdf` | Versão PDF dos filtros selecionados |
| `/category` | Informações de rating Glicko e categorias |
| `/sgf-library` | Biblioteca pública de SGF |
| `/sgf-library/<filename>` | Metadados de um registro SGF |
| `/sgf/<filename>` | Download de um registro SGF |
| `/matches/<match_id>/record` | Visualização do SGF vinculado a uma partida |
| `/matches/<match_id>/sgf` | Download do SGF vinculado a uma partida |
| `/news/<article_id>` | Artigo de notícia publicado |

A maioria das páginas aceita o parâmetro opcional `lang=es`, `lang=en` ou `lang=pt`. `/rankings` aceita `country` (ISO 3166-1 alpha-3; códigos alpha-2 legados também são reconhecidos), `region=latin-america-caribbean` e `group=u18|women`; é possível combinar esses filtros. Os relatórios também aceitam `start_date`, `end_date` e `player_id`; consulte o guia da interface para exemplos.

## Visibilidade e ratings dos torneios

Comportamento atual:

- `/tournaments` lista torneios ativos e concluídos. Diretores, operadores e administradores também podem incluir rascunhos com `show_drafts=1`; torneios cancelados são excluídos da lista.
- As páginas públicas de partidas e os perfis ocultam partidas vinculadas a torneios em rascunho ou cancelados. O histórico de torneios nos perfis de jogadores inclui apenas torneios concluídos.
- O recálculo de ratings inclui partidas independentes e partidas vinculadas a torneios concluídos. Partidas de torneios ativos, em rascunho ou cancelados não afetam os ratings.
- Alterar o status de um torneio de modo que partidas concluídas sejam incluídas ou removidas marca a data afetada mais antiga, sem recalcular os ratings imediatamente. Use o recálculo completo ou a atualização incremental em `/admin/ratings` para aplicar a alteração.

Essas regras se aplicam às partidas vinculadas por meio de um emparelhamento do torneio; partidas independentes continuam contando para os ratings. Consulte [FUTURE_FEATURES.md](../FUTURE_FEATURES.md) para outras propostas pendentes.

## Autenticação e rotas de membros

| Rota | Método | Acesso |
| --- | --- | --- |
| `/admin/login` | GET, POST | Entrar |
| `/admin/register` | GET, POST | Criar uma conta de membro |
| `/admin/forgot-password` | GET, POST | Solicitar um link de recuperação |
| `/admin/reset-password/<token>` | GET, POST | Concluir a recuperação |
| `/admin/report-results` | GET, POST | Enviar um resultado envolvendo o jogador vinculado |
| `/admin/profile` | GET, POST | Gerenciar o perfil da conta autenticada |
| `/admin/logout` | GET | Encerrar a sessão atual |

Os envios de membros permanecem pendentes até serem revisados. Antes da aprovação, não alteram partidas públicas, ratings ou relatórios.

## Grupos de rotas administrativas

Todas as rotas abaixo exigem uma conta autenticada e permissões conforme a função:

- `/admin`: painel administrativo
- `/admin/import`: importações de Excel, CSV e OpenGotha
- `/admin/backups`: criar, restaurar e excluir backups gerenciados
- `/admin/players`, `/admin/matches`: gestão de jogadores e partidas
- `/admin/ratings`, `/admin/categories`: configuração de ratings e categorias
- `/admin/tournaments`: criação e gestão de torneios
- `/admin/tournaments/<tournament_id>/...`: participantes, emparelhamentos, resultados, rodadas e exportação
- `/admin/sgf/...`: vinculação e exclusão de SGF
- `/admin/news`: gestão de notícias
- `/admin/users`, `/admin/audit`, `/admin/settings`: contas, auditoria e configurações

As rotas POST alteram o estado da aplicação e devem ser usadas pelos formulários renderizados. Ações de backup, restauração, importação, exclusão e torneios não são expostas como operações GET sem autenticação.

## Orientações para integrações

Para uma integração externa, use as exportações documentadas em CSV/PDF ou o banco de dados por meio de um processo operacional aprovado. Não faça scraping de formulários administrativos nem dependa de respostas JSON não documentadas.

O [roteiro de migração para uma API autenticada](../FUTURE_FEATURES.md#authenticated-api-migration) é o plano futuro de referência, consolidado a partir de Code Review v4. Ele propõe autenticação com tokens bearer para todos os recursos de `/api/v1` (incluindo leituras e OpenAPI/documentação), permissões e esquemas explícitos, paginação limitada, erros JSON e entrega gradual de consultas a envios de membros e operações da equipe. As páginas públicas e exportações existentes continuariam disponíveis. Essas rotas de API estão **planejadas, não implementadas**; este guia continua documentando apenas o comportamento atual.
