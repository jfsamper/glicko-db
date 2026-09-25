# Ajuda da interface

Consulte as [rotas e notas de integração](api_endpoints.pt.md) e o [README em português](../README.pt.md).
Você também pode abrir este guia pelo botão `?` na barra superior ou em `/help?lang=pt`.

## Índice

- [Interface pública](#interface-publica)
    - [Ranking e diretório de jogadores](#ranking-e-diretorio-de-jogadores)
    - [Partidas e visualizador SGF](#partidas-e-visualizador-sgf)
    - [Torneios públicos](#torneios-publicos)
    - [Relatórios periódicos e exportação](#relatorios-periodicos-e-exportacao)
    - [Conversor de categoria](#conversor-de-categoria)
    - [Notícias públicas](#noticias-publicas)
    - [Biblioteca SGF](#biblioteca-sgf)
- [Interface de membros](#interface-de-membros)
    - [Registro e início de sessão](#registro-e-inicio-de-sessao)
    - [Gestão de perfil e preferências](#gestao-de-perfil-e-preferencias)
    - [Recuperação de senha](#recuperacao-de-senha)
    - [Enviar resultados](#enviar-resultados)
- [Painel administrativo](#painel-administrativo)
    - [Gestão de torneios](#gestao-de-torneios)
        - [1. Criação e configuração de torneios (`/admin/tournaments`)](#1-criacao-e-configuracao-de-torneios-admintournaments)
        - [2. Gestão de participantes](#2-gestao-de-participantes)
        - [3. Emparelhamentos, rodadas e registro de resultados](#3-emparelhamentos-rodadas-e-registro-de-resultados)
        - [4. Processamento de rodadas e cálculo de rating](#4-processamento-de-rodadas-e-calculo-de-rating)
    - [Gestão de dados](#gestao-de-dados)
        - [1. Assistente de importação com prévia (`/admin/import`)](#1-assistente-de-importacao-com-previa-adminimport)
        - [2. Administração de jogadores (`/admin/players`)](#2-administracao-de-jogadores-adminplayers)
        - [3. Administração de partidas (`/admin/matches`)](#3-administracao-de-partidas-adminmatches)
        - [4. Configuração de ratings e categorias (`/admin/ratings` e `/admin/categories`)](#4-configuracao-de-ratings-e-categorias-adminratings-e-admincategories)
        - [5. Fila de moderação de resultados (`/admin/result-submissions`)](#5-fila-de-moderacao-de-resultados-adminresult-submissions)
    - [Tarefas administrativas e de segurança](#tarefas-administrativas-e-de-seguranca)
        - [1. Backups e restauração (`/admin/backups`)](#1-backups-e-restauracao-adminbackups)
        - [2. Gestão de contas de usuários (`/admin/users`)](#2-gestao-de-contas-de-usuarios-adminusers)
        - [3. Publicação de notícias (`/admin/news`)](#3-publicacao-de-noticias-adminnews)
        - [4. Registro e revisão de auditoria (`/admin/audit`)](#4-registro-e-revisao-de-auditoria-adminaudit)
        - [5. Configurações de segurança da aplicação (`/admin/settings`)](#5-configuracoes-de-seguranca-da-aplicacao-adminsettings)
- [Guia de solução de problemas](#guia-de-solucao-de-problemas)

## Interface pública

Não é necessária uma conta para `/`, `/rankings`, `/players`, `/player/view?id=<player_id>`, `/matches`, `/tournaments`, `/tournaments/<tournament_id>`, `/reports`, `/category` e `/sgf-library`. Essas páginas oferecem ranking, perfis, histórico de partidas, torneios públicos, classificações, informações de rating, relatórios e visualização ou download de SGF.

<a href="screenshots/public-home.png"><img src="screenshots/public-home.png" alt="Página inicial pública" width="560" /></a>

*A página pública oferece navegação, controles de idioma e tema, ranking, estatísticas e links para o conteúdo público.*

<a href="screenshots/public-players.png"><img src="screenshots/public-players.png" alt="Diretório de jogadores" width="560" /></a>

*O diretório permite explorar perfis e filtrar a lista pública de jogadores.*

As páginas aceitam `?lang=es`, `?lang=en` ou `?lang=pt`; use `AAAA-MM-DD` nos filtros e campos de data.

### Ranking e diretório de jogadores

- **Ranking (`/rankings`)**: exibe a tabela oficial com rating Glicko-2, desvio de rating (RD), categoria Dan/Kyu calculada, partidas jogadas, porcentagem de vitórias, sequência recente e data da última atividade. Apenas jogadores ativos são listados.
- **Diretório de jogadores (`/players`)**: busca por nome ou sobrenome, filtros por intervalo de rating mínimo e máximo, data da última atividade e ordenação por qualquer coluna com paginação configurável.
- **Perfil do jogador (`/player/view?id=<id>`)**:
  - Resumo de rating atual, RD, volatilidade e categoria.
  - Conquistas na carreira: melhor rating histórico, maior sequência de vitórias, vitórias como brancas e como pretas.
  - Histórico completo de partidas com data, cor, oponente, resultado, evento e link para o visualizador SGF quando disponível.
  - Gráfico interativo de evolução do rating ao longo do tempo.
  - Histórico de torneios com posição inicial e final.
  - Tabela de confrontos diretos (Head-to-Head) contra cada oponente.

<a href="screenshots/public-rankings.png"><img src="screenshots/public-rankings.png" alt="Tabela de ranking público" width="560" /></a>

*Tabela oficial de ranking ordenada por rating com indicadores de categoria e forma recente.*

<a href="screenshots/public-player-profile.png"><img src="screenshots/public-player-profile.png" alt="Perfil público de Acuña, Carlos" width="560" /></a>

*Perfil público com rating atual, categoria e resumo da atividade do jogador selecionado.*

### Partidas e visualizador SGF

- **Lista de partidas (`/matches`)**: histórico global de partidas registradas, com filtros por período e jogador, e ordenação por data, jogador branco, jogador preto, resultado ou rodada.
- **Visualizador SGF (`/matches/<id>/record` e `/sgf-library/<filename>`)**: visualizador interativo BesoGo integrado. Permite reproduzir jogadas lance a lance, alternar entre os temas Claro (Simples) e Escuro e baixar o arquivo original pelo link direto (`/matches/<id>/sgf` ou `/sgf/<filename>`).

<a href="screenshots/public-matches.png"><img src="screenshots/public-matches.png" alt="Lista pública de partidas" width="560" /></a>

*Lista global de partidas com filtros, link para a biblioteca SGF e ações de visualização.*

<a href="screenshots/public-match-record.png"><img src="screenshots/public-match-record.png" alt="Visualizador SGF de uma partida" width="560" /></a>

*Visualizador SGF de uma partida vinculada, com controles de reprodução, troca de tema e download.*

### Torneios públicos

- **Lista de torneios (`/tournaments`)**: exibe torneios ativos e concluídos com data, local, sistema de jogo, número de rodadas e status. Torneios em rascunho permanecem ocultos do público.
- **Detalhes do torneio (`/tournaments/<id>`)**: consulte emparelhamentos e resultados mesa por mesa de cada rodada disputada, além da tabela de classificação oficial atualizada com pontuação (Pts/MMS), SOS, SOSOS e SODOS.

<a href="screenshots/public-tournaments.png"><img src="screenshots/public-tournaments.png" alt="Torneios públicos" width="560" /></a>

*Lista de torneios públicos com status, sistema e número de rodadas.*

<a href="screenshots/public-tournament-detail.png"><img src="screenshots/public-tournament-detail.png" alt="Detalhes públicos do torneio" width="560" /></a>

*Detalhes de um torneio com seletor de rodada, mesas emparelhadas, resultados e classificação.*

### Relatórios periódicos e exportação

- **Tela de relatórios (`/reports`)**: analisa o desempenho dos jogadores em períodos específicos:
  - Períodos predefinidos: *Todo o período*, *Este ano*, *Este trimestre* ou *Intervalo personalizado*.
  - Filtro opcional por jogador para examinar o desempenho contra adversários, clubes e países no período.
  - As datas `start_date` e `end_date` são inclusivas e avaliadas no fuso horário fixo do servidor (UTC-5).
  - Partidas com datas ou resultados inválidos são excluídas e contabilizadas no resumo.
- **Exportação CSV (`/reports/export.csv`)**: gera uma planilha CSV com a tabela exata calculada na tela, mantendo os filtros aplicados.
- **Exportação PDF (`/reports/export.pdf`)**: gera um relatório em PDF formatado com cabeçalhos centralizados, data de geração, idioma atual e o nome do jogador e período no arquivo.

<a href="screenshots/public-reports.png"><img src="screenshots/public-reports.png" alt="Relatórios e estatísticas" width="560" /></a>

*Relatórios periódicos com filtros de data e jogador, além de opções de exportação em CSV e PDF.*

### Conversor de categoria

A página `/category` converte um rating Glicko em categoria Dan/Kyu e exibe a fórmula, as constantes configuradas e a escala completa.

<a href="screenshots/public-category.png"><img src="screenshots/public-category.png" alt="Conversor público de categoria" width="560" /></a>

*Conversor de rating para categoria com campo de entrada e botão de cálculo.*

### Notícias públicas

A página `/news/<article_id>` exibe o artigo publicado e transforma tags de jogadores, torneios e partidas em links relacionados.

<a href="screenshots/public-news-article.png"><img src="screenshots/public-news-article.png" alt="Artigo público de notícias" width="560" /></a>

*Artigo publicado com links relacionados a jogadores, torneios e partidas.*

### Biblioteca SGF

A biblioteca pública (`/sgf-library`) indexa todos os registros de partidas enviados:
- Valida tamanho do arquivo, codificação UTF-8 e estrutura sintática SGF.
- Sincroniza automaticamente as propriedades do cabeçalho SGF (jogadores branco e preto, graduações, evento, data e resultado) com a partida associada no banco de dados.
- Se um arquivo físico for removido do disco, links obsoletos no banco de dados são limpos automaticamente para evitar URLs quebradas.
- Desvincular um arquivo ou excluir a partida mantém o registro na biblioteca; apenas administradores podem excluir arquivos físicos definitivamente.

<a href="screenshots/public-sgf-library.png"><img src="screenshots/public-sgf-library.png" alt="Biblioteca pública de arquivos SGF" width="560" /></a>

*Biblioteca SGF com metadados, partidas vinculadas e links para o visualizador.*

<a href="screenshots/public-sgf-record.png"><img src="screenshots/public-sgf-record.png" alt="Registro SGF da biblioteca" width="560" /></a>

*Registro SGF aberto pela biblioteca, com seus metadados e controles do visualizador.*

## Interface de membros

Os jogadores da comunidade podem registrar uma conta pessoal para gerenciar preferências e enviar resultados de torneios e partidas de clube.

### Registro e início de sessão

1. Abra `/admin/register` para criar uma conta com nome de usuário, e-mail e senha (mínimo de 8 caracteres). O formulário é protegido pelo Google reCAPTCHA v3.
2. Contas novas recebem automaticamente a função `member`.
3. Solicite a um administrador ou operador que vincule sua conta ao seu registro de jogador em `/admin/users`.
4. Entre em `/admin/login`.

<a href="screenshots/admin-login.png"><img src="screenshots/admin-login.png" alt="Login administrativo" width="560" /></a>

*Formulário de acesso administrativo com links de registro e recuperação.*

<a href="screenshots/admin-register.png"><img src="screenshots/admin-register.png" alt="Registro de conta de membro" width="560" /></a>

*Formulário de registro de membro com e-mail e confirmação de senha.*

### Gestão de perfil e preferências

Em `/admin/profile`, o usuário autenticado pode:
- Atualizar o endereço de e-mail para notificações e recuperação.
- Escolher o idioma preferido da interface (`Español`, `English`, `Português`).
- Alternar entre o tema claro e o tema escuro.
- Selecionar seu fuso horário IANA com ajuste UTC calculado (por exemplo, `America/Sao_Paulo [UTC-03:00]`, `America/Bogota`, `Europe/Lisbon`).
- Alterar sua senha informando a senha atual e confirmando a nova.

<a href="screenshots/member-profile.png"><img src="screenshots/member-profile.png" alt="Perfil do membro" width="560" /></a>

*Configurações de perfil do usuário para gerenciar idioma, tema, fuso horário e senha.*

### Recuperação de senha

1. Caso esqueça sua senha, clique em *Esqueceu sua senha?* em `/admin/login` ou acesse `/admin/forgot-password`.
2. Informe seu e-mail cadastrado. Se existente, o sistema enviará um link de uso único com token hash criptográfico.
3. O link expira conforme o tempo configurado em `PASSWORD_RESET_TTL_SECONDS` (padrão: 3600 segundos / 1 hora).
4. Abra o link `/admin/reset-password/<token>` e defina a nova senha. Por segurança, a resposta nunca revela se o e-mail está cadastrado.

<a href="screenshots/admin-forgot-password.png"><img src="screenshots/admin-forgot-password.png" alt="Solicitação de recuperação de senha" width="560" /></a>

*Solicitação de um link de recuperação usando o e-mail cadastrado.*

<a href="screenshots/admin-reset-password.png"><img src="screenshots/admin-reset-password.png" alt="Formulário de redefinição de senha" width="560" /></a>

*Formulário para definir uma nova senha a partir de um token.*

### Enviar resultados

Membros podem reportar resultados de partidas em `/admin/report-results`:

1. **Requisito obrigatório**: a conta de usuário deve estar vinculada a um jogador ativo por um administrador.
2. **Formulário de envio**:
    - **Oponente**: selecione o jogador adversário na lista de jogadores ativos.
    - **Cor**: indique se jogou de Brancas ou Pretas.
    - **Resultado**: vitória de Brancas (`1-0`), vitória de Pretas (`0-1`) ou Empate (`1/2-1/2`).
    - **Data**: data em que a partida foi disputada (`AAAA-MM-DD`).
    - **Evento e Local**: nome do torneio ou clube e cidade da partida.
    - **Rodada**: número da rodada, se aplicável.
    - **Pedras de handicap**: quantidade de pedras (0 a 9) concedidas às Pretas.
    - **Arquivo SGF (opcional)**: envie o arquivo `.sgf` com o registro das jogadas.
3. **Fila de moderação**: as partidas enviadas entram no estado *Pendente* na fila de moderação (`/admin/result-submissions`). Elas não afetam rankings, ratings ou relatórios públicos até serem revisadas e aprovadas pela equipe administrativa.

<a href="screenshots/member-report-results.png"><img src="screenshots/member-report-results.png" alt="Envio de resultados de membros" width="560" /></a>

*A tela mostra a exigência de vincular um jogador e a área de envios pendentes.*

## Painel administrativo

Entre em `/admin/login` com uma conta administrativa. A navegação disponível se adapta à função da conta:
- `member`: configurações de perfil e envio de resultados do jogador vinculado.
- `tournament_director`: gestão completa de torneios (participantes, emparelhamentos, rodadas, resultados e exportações).
- `operator`: todas as funções de torneios, mais gestão de partidas, importações de dados, biblioteca SGF, notícias e moderação de resultados.
- `administrator`: controle total da plataforma, incluindo jogadores, configurações de rating/categorias, backups, gestão de usuários, auditoria e parâmetros do sistema.

<a href="screenshots/admin-dashboard.png"><img src="screenshots/admin-dashboard.png" alt="Painel administrativo" width="560" /></a>

*O painel agrupa os controles em operações de torneios, gestão de dados e administração e acesso. Selecione **Abrir** em um cartão para entrar.*

### Gestão de torneios

#### 1. Criação e configuração de torneios (`/admin/tournaments`)

- **Nome, local e datas**: informe o título oficial, cidade/sede e datas de início e término.
- **Número de rodadas**: defina o total de rodadas planejadas.
- **Sistema de emparelhamento**:
  - **Suíço padrão (`swiss`)**: emparelha participantes com pontuações próximas, evitando repetição de adversários e equilibrando as cores branca e preta.
  - **Suíço por categoria (`swiss_cat`)**: restringe emparelhamentos estritamente dentro da mesma categoria nas rodadas iniciais indicadas.
  - **Suíço acelerado (`accelerated_swiss`)**: divide os participantes em faixas de aceleração (esquema de 3 faixas Go ou limites de categoria personalizados) nas rodadas iniciais para acelerar a convergência.
  - **McMahon (`mcmahon`)**: atribui pontuação inicial (MMS) com base na graduação a partir da Barra McMahon (`mm_bar`), Piso McMahon (`mm_floor`) e Zero McMahon (`mm_zero`).
- **Pontuação de folgas (BYE) e ausências**:
  - *Pontos por descanso (BYE)*: pontos atribuídos ao participante sem par quando o número for ímpar (padrão: 1.0 ponto).
  - *Pontos por ausência*: pontos concedidos em partidas não jogadas por ausência (padrão: 0.0 pontos).
- **Handicap automático**: habilita ou desabilita a sugestão automática de pedras calculada pela diferença de categoria.

<a href="screenshots/admin-tournaments.png"><img src="screenshots/admin-tournaments.png" alt="Administração de torneios" width="560" /></a>

*A página de torneios oferece controles para criar torneios, selecionar o sistema de emparelhamento e abrir suas operações.*

<a href="screenshots/admin-tournament-settings.png"><img src="screenshots/admin-tournament-settings.png" alt="Configurações avançadas do torneio" width="560" /></a>

*Configurações do torneio com datas, rodadas, pontos de BYE/ausência, handicap e aceleração.*

#### 2. Gestão de participantes

- **Inscrição de jogadores**: adicione jogadores ativos existentes pelo seletor ou crie novos jogadores diretamente no torneio.
- **Retirada de participantes**: remova participantes antes do início da primeira rodada.
- **Importação OpenGotha XML**: ao importar um arquivo OpenGotha, os participantes são carregados automaticamente com graduação, categoria e rating inicial. Se houver nomes parecidos com registros existentes, um assistente interativo permite vinculá-los, criar novos ou descartá-los.

<a href="screenshots/admin-tournament-players.png"><img src="screenshots/admin-tournament-players.png" alt="Gestão de participantes do torneio" width="560" /></a>

*Gestão de participantes com a lista atual, seletor de jogadores existentes e criação de jogadores pendentes.*

#### 3. Emparelhamentos, rodadas e registro de resultados

- **Gerar rodada**: o algoritmo emparelha os jogadores automaticamente respeitando o sistema escolhido, evitando repetições e alternando cores.
- **Tratamento de folgas (BYE)**: quando o número for ímpar, um BYE é atribuído automaticamente. O histórico é registrado para não repetir o BYE em um jogador enquanto outros ainda não o receberam.
- **Emparelhamento manual e assistido**:
  - *Emparelhar selecionados*: selecione dois jogadores livres e clique em *Emparelhar selecionados*.
  - *Desemparelhar / Desemparelhar tudo*: desfaça uma mesa específica ou reinicie a rodada inteira.
- **Ajuste de handicap por mesa**: cada mesa exibe a sugestão de pedras calculada automaticamente; o diretor pode editar as pedras antes de salvar o resultado.
- **Ciclo de 7 estados de resultados**: clique no nome do vencedor ou no texto do resultado para avançar entre:
  1. `-` : Pendente (partida ainda não jogada).
  2. `1-0` : Vitória das Brancas.
  3. `1/2-1/2` : Empate.
  4. `0-1` : Vitória das Pretas.
  5. `1-!0` : Vitória das Brancas por ausência das Pretas.
  6. `!0-1` : Vitória das Pretas por ausência das Brancas.
  7. `!0-0` : Ausência de ambos os jogadores (dupla derrota).
  *Clicar novamente no vencedor selecionado limpa o resultado de volta para pendente (`-`).*

<a href="screenshots/admin-tournament-detail.png"><img src="screenshots/admin-tournament-detail.png" alt="Detalhe do torneio e rodadas" width="560" /></a>

*Painel de controle de rodada do torneio: emparelhamentos, mesas, seletor de resultados e classificação.*

#### 4. Processamento de rodadas e cálculo de rating

- **Processar rodada (`Procesar ronda`)**:
  - Converte automaticamente os resultados jogados (`1-0`, `1/2-1/2`, `0-1`) em partidas oficiais no banco de dados.
  - Ausências (`1-!0`, `!0-1`, `!0-0`) contam para a classificação e desempates do torneio, mas **nunca** afetam ratings oficiais nem perfis de jogadores.
  - Aplica o ajuste logarítmico de rating por pedra quando a partida for com handicap.
  - Dispara o recálculo incremental de ratings em ordem cronológica por data e rodada.
- **Classificação e desempates**: as posições são estritamente sequenciais e únicas. Empates em pontos ou MMS são resolvidos de forma determinística por SOS, SOSOS, SODOS, rating base e ordem alfabética.
- **Ordem e migrações**: no mesmo dia, as atualizações de rating respeitam a rodada e depois a ordem de inserção. As notas de rodada são armazenadas como inteiros quando possível; rodadas desconhecidas usam a rodada 1. As tabelas de torneios são migradas automaticamente na inicialização.
- **Ciclo de vida do torneio**:
  - *Rascunho (`draft`)*: oculto do portal público durante a preparação.
  - *Ativo (`active`)*: publicado e visível com emparelhamentos e classificação em tempo real.
  - *Concluído (`completed`)*: torneio encerrado com classificação final oficial.
  - *Cancelado (`canceled`)*: cancelado sem efeito nos ratings.
- **Exportação de resultados**: baixe os resultados do torneio em formatos compatíveis para relatórios e sistemas externos.

### Gestão de dados

#### 1. Assistente de importação com prévia (`/admin/import`)

Importe planilhas históricas, partidas ou arquivos do OpenGotha:
- **Formatos aceitos**:
  - Planilha Excel (`.xlsx` ou `.xls`): substitui o conjunto de dados ativo com jogadores e partidas completos.
  - XML do OpenGotha (`.xml`): importa metadados do torneio, participantes, emparelhamentos e pedras de handicap.
  - Arquivo CSV (`.csv`): colunas `date`, `white`, `black`, `result` e opcional `handicap` (0 a 9).
- Crie um backup antes de importar uma planilha que substitua os dados ativos.
- **Reconciliação explícita de jogadores**:
  - A prévia classifica cada registro como *Correspondência exata*, *Sugestão aproximada*, *Novo jogador* ou *Duplicado*.
  - O operador decide para cada jogador: vincular ao existente, criar novo ou rejeitar a linha.
  - Metadados do torneio podem ser revisados e editados antes da confirmação.
  - Erros de validação em arquivos CSV cancelam toda a operação sem efetuar alterações parciais no banco.

<a href="screenshots/admin-import.png"><img src="screenshots/admin-import.png" alt="Controles de importação" width="560" /></a>

*A tela usa o seletor de arquivos para escolher um arquivo Excel, CSV ou OpenGotha e **Importar** para iniciar a prévia e reconciliação.*

<a href="screenshots/admin-import-preview.png"><img src="screenshots/admin-import-preview.png" alt="Prévia e reconciliação de importação" width="560" /></a>

*Prévia do OpenGotha com metadados editáveis, resumo das correspondências e decisões por jogador.*

#### 2. Administração de jogadores (`/admin/players`)

- Filtre jogadores por nome, faixa de rating e status ativo/inativo.
- **Editar jogador (`/admin/players/edit?id=<id>`)**: atualize nome, sobrenome, nome de exibição, rating inicial, RD inicial e ative ou desative o jogador. Jogadores inativos são excluídos automaticamente de rankings e emparelhamentos.
- **Excluir jogador**: modal de confirmação seguro que avisa sobre a exclusão do histórico e partidas associadas.

<a href="screenshots/admin-players.png"><img src="screenshots/admin-players.png" alt="Lista administrativa de jogadores" width="560" /></a>

*Lista administrativa com filtros, faixa Glicko, status e acessos de edição.*

<a href="screenshots/admin-edit-player.png"><img src="screenshots/admin-edit-player.png" alt="Edição de Acuña, Carlos" width="560" /></a>

*Formulário de edição com nome, rating inicial, clube, país e status ativo.*

#### 3. Administração de partidas (`/admin/matches`)

- Lista paginada de todas as partidas com filtros de data e jogador.
- **Adicionar partida (`/admin/matches/add`)**: formulário com validação rigorosa de data (`AAAA-MM-DD`), jogadores distintos existentes, resultado válido (`1-0`, `0-1`, `1/2-1/2`), pedras de handicap (0-9) e envio opcional de SGF.
- **Editar partida (`/admin/matches/edit?id=<id>`)**: permite corrigir jogadores, datas, resultados ou anotações de rodada.
- Quando resultados de torneio viram partidas oficiais, `event` mantém o nome do torneio e `notes` (exibido como `Round`) armazena a rodada como inteiro. Valores antigos são convertidos quando contêm um número; textos sem número são mantidos e tratados como rodada 0.
- **Vincular e desvincular SGF**: vincule arquivos SGF da biblioteca ou envie novos registros sincronizados.

<a href="screenshots/admin-match-form-add.png"><img src="screenshots/admin-match-form-add.png" alt="Formulário para adicionar uma partida" width="560" /></a>

*Formulário de nova partida com jogadores, resultado, data, rodada, handicap e envio opcional de SGF.*

<a href="screenshots/admin-match-form-edit.png"><img src="screenshots/admin-match-form-edit.png" alt="Formulário para editar uma partida" width="560" /></a>

*Formulário pré-preenchido para corrigir uma partida existente.*

<a href="screenshots/admin-matches.png"><img src="screenshots/admin-matches.png" alt="Administração de partidas" width="560" /></a>

*Tabela de partidas com filtros por data, jogador e ferramentas de edição, exclusão e gestão de SGF.*

#### 4. Configuração de ratings e categorias (`/admin/ratings` e `/admin/categories`)

- **Parâmetros Glicko-2**:
  - Rating inicial padrão (ex.: 1500).
  - Desvio de rating inicial (RD, ex.: 350).
  - Volatilidade inicial ($\sigma$, ex.: 0.06).
  - Constante de tempo do sistema ($\tau$, ex.: 0.5).
- **Conversão de categorias Dan/Kyu**:
  - Constante de escala $k$ e deslocamento $m$.
  - Limites mínimos para níveis Dan e Kyu.
- **Recálculo de ratings**:
  - *Recálculo completo*: reprocessa todo o histórico cronologicamente por data e rodada.
  - *Atualização incremental*: recalcula instantâneos a partir da data modificada mais antiga (*dirty date*).

<a href="screenshots/admin-ratings.png"><img src="screenshots/admin-ratings.png" alt="Configuração administrativa de ratings" width="560" /></a>

*Painel de ratings com parâmetros Glicko-2, contagens do histórico e ações de recálculo.*

<a href="screenshots/admin-categories.png"><img src="screenshots/admin-categories.png" alt="Configuração administrativa de categorias" width="560" /></a>

*Configuração da fórmula Dan/Kyu com prévia das alterações de categoria.*

#### 5. Fila de moderação de resultados (`/admin/result-submissions`)

- Revise partidas enviadas pelos membros da comunidade.
- Inspecione detalhes completos: remetente, oponente, cores, resultado, data, handicap e visualizador do SGF anexo.
- **Aprovar**: materializa a partida no banco de dados oficial e atualiza os ratings automaticamente.
- **Rejeitar**: descarta o envio permitindo registrar uma nota de revisão visível para auditoria.

<a href="screenshots/admin-result-submissions.png"><img src="screenshots/admin-result-submissions.png" alt="Fila administrativa de resultados" width="560" /></a>

*Fila de moderação com filtro de status, detalhes da partida e ações de aprovar ou rejeitar.*

### Tarefas administrativas e de segurança

#### 1. Backups e restauração (`/admin/backups`)

- **Criar backup**: gera um backup SQLite com data e nome seguro em `backups/`, incluindo o diretório auxiliar de SGF.
- **Restaurar backup**:
  - Restaura o banco de dados selecionado e executa migrações pendentes de esquema.
  - Reconstrói os artefatos e o índice de busca textual FTS5 (`players_fts`).
  - Restaura a biblioteca SGF sem quebrar vínculos.
  - Restringe candidatos a arquivos gerenciados e ao backup `.bak` designado.
- **Excluir backups**: remove cópias antigas para liberar espaço de armazenamento.
- Execute `python scripts/check_legacy_players_state.py` para auditar o banco ativo e os backups gerenciados.

<a href="screenshots/admin-backups.png"><img src="screenshots/admin-backups.png" alt="Gestão de backups" width="560" /></a>

*Lista de backups com ações de criar, restaurar e excluir.*

#### 2. Gestão de contas de usuários (`/admin/users`)

- Crie e edite contas de usuário com funções (`administrator`, `operator`, `tournament_director`, `member`).
- Vincule contas de usuário a perfis de jogadores para habilitar o envio de resultados.
- Atribua fusos horários individuais a cada conta.
- Ative ou desative contas de usuário.

<a href="screenshots/admin-users.png"><img src="screenshots/admin-users.png" alt="Lista de contas de usuário" width="560" /></a>

*Lista de contas com função, jogador vinculado, fuso horário, status e ações.*

<a href="screenshots/admin-create-user.png"><img src="screenshots/admin-create-user.png" alt="Criação de conta de usuário" width="560" /></a>

*Formulário para criar uma conta e atribuir função, jogador vinculado e fuso horário.*

<a href="screenshots/admin-edit-user.png"><img src="screenshots/admin-edit-user.png" alt="Edição de docs_admin vinculado a Acuña, Carlos" width="560" /></a>

*Edição de `docs_admin`, mostrando o vínculo com o jogador Acuña, Carlos e as permissões da conta.*

#### 3. Publicação de notícias (`/admin/news`)

- Redija notícias com título, texto e status de publicação (Rascunho ou Publicada).
- Insira tags inteligentes no texto: `[player:12]`, `[tournament:5]`, `[match:104]`, renderizadas na visualização pública como links interativos e atalhos para o visualizador SGF.

<a href="screenshots/admin-news.png"><img src="screenshots/admin-news.png" alt="Lista administrativa de notícias" width="560" /></a>

*Lista de notícias com status de publicação, data de atualização e ações.*

<a href="screenshots/admin-news-form.png"><img src="screenshots/admin-news-form.png" alt="Nova notícia" width="560" /></a>

*Formulário de nova notícia com publicação imediata e links inteligentes.*

<a href="screenshots/admin-news-edit.png"><img src="screenshots/admin-news-edit.png" alt="Edição de notícia" width="560" /></a>

*Formulário pré-preenchido para editar o texto, status e links relacionados de uma notícia.*

#### 4. Registro e revisão de auditoria (`/admin/audit`)

- Registro completo de todas as ações administrativas que alteram o estado do sistema: importações, ciclo de vida de torneios, partidas, alterações de rating/categorias, usuários e backups.
- Filtre por usuário autor, categoria de ação, busca livre de texto e período de datas.
- Resumo compacto em JSON limitado a 2 KiB por evento com descarte automático após 730 dias (`AUDIT_RETENTION_DAYS`).

<a href="screenshots/admin-audit.png"><img src="screenshots/admin-audit.png" alt="Revisão de auditoria" width="560" /></a>

*Registro de auditoria administrativa com filtros por usuário, categoria de ação e datas.*

#### 5. Configurações de segurança da aplicação (`/admin/settings`)

- Ajuste o número máximo de tentativas de login falhas antes do bloqueio temporário por IP.
- Defina a janela de tempo da limitação de taxa (em segundos).
- Configure a duração (TTL) dos links de recuperação de senha.
- Botão de restauração para restabelecer os valores padrão definidos em `config.py`.

<a href="screenshots/admin-settings.png"><img src="screenshots/admin-settings.png" alt="Configurações de segurança" width="560" /></a>

*Parâmetros administrativos de tentativas de login, janela de limitação e TTL de recuperação.*

### Guia de solução de problemas

1. **Acesso negado (403)**: verifique se sua conta possui a função necessária (`tournament_director`, `operator` ou `administrator`) para a área solicitada.
2. **Não consigo enviar resultados como membro**: confirme em `/admin/users` se sua conta está vinculada a um registro de jogador ativo.
3. **Erros na importação de arquivos**:
   - Certifique-se de que o CSV use codificação UTF-8 e contenha os cabeçalhos obrigatórios `date`, `white`, `black`, `result`.
   - Verifique se o formato de data é `AAAA-MM-DD` e os resultados são exclusivamente `1-0`, `0-1` ou `1/2-1/2`.
   - Verifique se as pedras de handicap estão no intervalo de 0 a 9.
4. **Arquivos SGF não encontrados**: se um arquivo SGF for excluído do disco, o sistema limpa o vínculo automaticamente. Envie o arquivo novamente em `/admin/matches` ou na biblioteca SGF.
5. **Restauração de banco de dados**: após restaurar um backup, aguarde o término das migrações e da indexação FTS5 antes de iniciar operações.
6. **Segurança em produção**: mantenha segredos (`APP_SECRET_KEY`, `ADMIN_PASSWORD`, credenciais SMTP e chaves reCAPTCHA) em variáveis de ambiente do servidor, nunca no controle de versão.
