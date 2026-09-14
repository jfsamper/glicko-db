# Glicko DB

Documentação: [Español](README.md) | [English](README.en.md) | Português

Glicko DB é uma aplicação Flask e SQLite para gerenciar jogadores, ratings, partidas e torneios de uma comunidade de Go. Ela oferece classificações e estatísticas públicas, além de telas administrativas protegidas para importações, configuração do rating, cópias de segurança e operações de torneios.

## Recursos

- Classificações públicas, busca de jogadores, perfis, histórico de partidas, gráficos de rating e conversão de categoria
- Cálculo Glicko-2 com parâmetros configuráveis de rating e categoria
- Interface pública em espanhol, inglês e português
- Administração de jogadores e partidas com paginação, filtros e ordenação consistente
- Biblioteca pública de registros SGF, com vinculação e desvinculação de partidas para diretores de torneio, operadores e administradores
- Importação de livros Excel (XLSX), OpenGotha XML e arquivos de partidas em CSV
- Criação e edição de torneios, importação de OpenGotha, emparelhamentos, registro de resultados, classificação e exportação
- Registro de contas de membros e envio de resultados individuais para aprovação administrativa
- Relatórios públicos por período (por padrão, todo o tempo) com filtros por jogador, exportação CSV/PDF localizada, mudanças de rating e desempenho por oponente, país e clube
- Sistemas suíço, suíço por categoria, suíço acelerado e McMahon
- Tratamento de BYE e ausências, cópias de segurança, proteções de restauração e migrações SQLite
- Torneios em rascunho ocultos das listagens públicas, com opção administrativa para mostrar rascunhos
- Partidas com handicap em pedras (estilo Go), com sugestão automática pela diferença de categoria e ajuste de rating no estilo OGS

## Requisitos

- Python 3.10 ou posterior
- `pip`
- Pacotes Python:
  - `Flask>=3.0`
  - `Flask-WTF>=1.2`
  - `Werkzeug>=3.0`
  - `openpyxl>=3.1`
  - `reportlab>=4.0`
  - `Pillow==11.3.0` (necessário pelo ReportLab para geração de PDF)
  - `tzdata>=2024.1` (dados de fuso horário no Windows)

## Execução local

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:APP_SECRET_KEY = "substitua-por-um-valor-aleatorio-longo"
$env:ADMIN_PASSWORD = "escolha-uma-senha-segura"
python app.py
```

macOS ou Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export APP_SECRET_KEY="substitua-por-um-valor-aleatorio-longo"
export ADMIN_PASSWORD="escolha-uma-senha-segura"
python app.py
```

Abra `http://127.0.0.1:5000` no navegador. A aplicação cria o banco SQLite em `data/acg_ratings.db` na primeira inicialização.

Apenas para dados de exemplo locais, defina `LOAD_SAMPLE_DATA=1` antes de iniciar. Não use dados de exemplo em um banco de produção.

## Configuração

Os valores padrão estão em `config.py`.

- `APP_SECRET_KEY`: chave de assinatura da sessão Flask. Deve ser configurada em produção.
- `ADMIN_PASSWORD`: senha de acesso administrativa atual. Deve ser substituída em produção.
- `LOAD_SAMPLE_DATA=1`: importa `rank-final.xlsx` se existir e substitui o conjunto de dados atual; serve apenas para desenvolvimento local.
- `DB_PATH`: local do banco SQLite, definido em `config.py`.
- `AUDIT_RETENTION_DAYS`: número de dias para manter eventos de auditoria; o padrão é `730`.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS` e `MAIL_FROM`: configurações SMTP para recuperação de senha; `PASSWORD_RESET_TTL_SECONDS` controla a expiração do link e usa 3600 segundos por padrão.
- `RECAPTCHA_SITE_KEY` e `RECAPTCHA_SECRET_KEY`: chaves do Google reCAPTCHA v3 para o formulário de registro de membros. A chave secreta é verificada no servidor e nunca deve ser exposta ao navegador.
- `RECAPTCHA_MIN_SCORE`: pontuação v3 mínima aceita para o registro; o padrão é `0.5`.
- `RECAPTCHA_EXPECTED_HOSTNAME`: verificação opcional do hostname na resposta; deixe vazio se a chave servir para vários hostnames configurados.

As datas e horas geradas pela aplicação usam UTC-5 por padrão. Cada conta pode escolher um fuso horário IANA no gerenciamento de usuários; contas sem preferência mantêm UTC-5. Os seletores de fuso horário mostram um representante para cada deslocamento UTC comum atual, ordenado do menor para o maior. O Python usa os dados IANA do sistema no Linux, enquanto `tzdata` fornece o fallback portátil no Windows ou em imagens Linux mínimas. Se o fuso salvo de uma conta não puder ser carregado, a apresentação volta para UTC-05:00. Ao calcular ratings, as partidas do mesmo dia são processadas por número de rodada e depois pela ordem de inserção; rodadas desconhecidas são tratadas como rodada 1.

Os relatórios em `/reports` usam intervalos inclusivos `start_date` e `end_date`, e a pertença ao período é determinada com o fuso horário fixo do servidor (UTC-5 por padrão), e não com o fuso da conta. A visão geral e os relatórios filtrados por jogador começam em Todo o período. A porcentagem de vitórias é vitórias divididas por partidas. Cada linha mostra mudança absoluta de pontos, variação percentual e mudança inteira de categoria. O seletor de jogadores é ordenado pelo número total de partidas. Os totais são calculados no servidor uma vez e reutilizados na página e nas exportações CSV/PDF; os rótulos e textos do PDF seguem o idioma atual, e registros com data ou resultado inválidos são excluídos e contabilizados. Partidas materializadas de torneios conservam uma identidade única por emparelhamento para impedir importações ou contagens duplicadas.

O painel de administração usa contas nomeadas com quatro papéis: `administrator`, `tournament_director`, `operator` e `member`. Contas novas se registram como `member`; um administrador pode vinculá-las manualmente a um jogador em `/admin/users`. Membros só podem enviar resultados envolvendo seu jogador vinculado. Diretores, operadores e administradores revisam a fila em `/admin/result-submissions`, e apenas resultados aprovados viram partidas públicas. Se não existir nenhuma conta, a aplicação cria um administrador inicial usando `ADMIN_PASSWORD` na primeira inicialização; contas adicionais e fusos horários são gerenciados em `/admin/users`. Cada usuário pode abrir `/admin/profile` para salvar idioma, tema, fuso horário, e-mail e senha. O link de recuperação em `/admin/login` usa tokens de uso único e respostas que não revelam se um e-mail existe; configure SMTP em produção. Tentativas falhas são limitadas. Em produção, use HTTPS e senhas fortes e exclusivas. A autorização é baseada na sessão do usuário e nas permissões. Apenas `administrator` e `operator` podem modificar jogadores, ratings e categorias; `tournament_director` mantém as operações de torneios.

Administradores podem ajustar o número máximo de tentativas de login, a janela de limitação e a duração do link de recuperação em `/admin/settings`. Esses valores são armazenados no SQLite, e o botão de restauração usa os valores iniciais de `config.py`. `ADMIN_PASSWORD`, caminhos e credenciais SMTP continuam sendo configuração do ambiente.

A gestão de registros SGF segue as permissões da conta: `administrator`, `tournament_director` e `operator` podem vinculá-los ou desvinculá-los, enquanto apenas `administrator` pode excluí-los.

## Plano do projeto

O plano detalhado e priorizado está em [FUTURE_FEATURES.md](FUTURE_FEATURES.md). A reconciliação explícita da importação, os payloads tipados do OpenGotha, a revisão administrativa por conta com busca livre e filtros por data, a melhoria do perfil do jogador e o modal explícito para excluir torneios estão implementados e verificados. Os perfis incluem atividade recente, sequências, histórico de torneios e filtro de temporada.

## Operações comuns

### Importar ratings e partidas

1. Entre em `/admin/login`.
2. Abra a tela de importação.
3. Envie um dos formatos compatíveis:
   - `.xlsx` ou `.xls`: importa os dados e substitui o conjunto atual.
   - OpenGotha `.xml`: importa partidas e metadados do torneio. O atributo `handicap` de cada partida (quantidade de pedras para Preto) é preservado quando presente.
   - `.csv` com as colunas `date`, `white`, `black` e `result`. Uma coluna opcional `handicap` (pedras, 0-9) é preservada; valores ausentes ou inválidos usam 0.
4. Confirme as classificações e os perfis de jogadores resultantes.

Mantenha uma cópia de segurança antes de importar uma planilha que substitua os dados.

### Gerenciar um torneio

1. Na administração, crie um torneio ou importe um arquivo XML do OpenGotha.
2. Adicione participantes e escolha suíço, suíço por categoria, suíço acelerado ou McMahon.
3. Gere ou administre manualmente os emparelhamentos de cada rodada.
4. Na tela do torneio, edite nome, local, número de rodadas, pontos de BYE e pontos de ausência.
5. Registre resultados clicando no nome do jogador vencedor ou no texto do resultado. O texto percorre `-`, `1-0`, `1/2-1/2`, `0-1`, `1-!0`, `!0-1` e `!0-0`; clicar novamente no vencedor selecionado limpa o resultado. Os três últimos registram, respectivamente, ausência das pretas, ausência das brancas ou ausência de ambos. Eles contam para a classificação do torneio, mas não para ratings, perfis ou resumos de jogadores. O vencedor aparece destacado em negrito e verde.
6. Registre BYEs, gere a rodada seguinte, revise a classificação e exporte os resultados com os botões administrativos.

Registros SGF opcionais enviados com uma partida são armazenados em `uploads/sgf/` com um nome seguro gerado automaticamente. Ao salvar, suas propriedades principais são ajustadas para correspondem aos jogadores, graduações, local/evento, data e resultado no banco de dados. Registros aprovados via `Reportar Resultados` seguem o mesmo processo.

As posições da classificação são sempre únicas e sequenciais; empates são resolvidos por SOS, SOSOS, SODOS, rating e nome. O algoritmo de emparelhamento evita repetir BYE para um jogador enquanto outro participante ainda não o recebeu, e BYEs importados do OpenGotha são registrados para que rodadas futuras respeitem esse histórico.

Quando uma importação do OpenGotha encontra um nome semelhante, ela mostra uma sugestão de jogador do banco de dados. Clique no nome sugerido para vinculá-lo imediatamente ao jogador existente, ou use o seletor para criar um novo jogador ou escolher outro.

Cada emparelhamento recebe uma sugestão automática de handicap em pedras (uma pedra por categoria de diferença entre os jogadores), que o diretor do torneio pode editar antes de registrar o resultado. Quando a rodada é processada, o handicap é transferido para a partida e desloca o rating efetivo exatamente uma categoria logarítmica por pedra: o rating efetivo das pretas sobe e o das brancas cai apenas nesse cálculo, sem alterar os ratings base.

### Biblioteca SGF

A biblioteca pública está disponível em `/sgf-library`. Registros SGF opcionais são armazenados em `uploads/sgf/` com um nome seguro gerado pela aplicação e podem ser vistos ou baixados publicamente.

Ao enviar ou vincular um registro, a aplicação valida o tamanho, a codificação UTF-8 e a estrutura SGF, e atualiza suas propriedades principais para corresponderem aos jogadores, cores, graduações, local/evento, data e resultado da partida. Se um arquivo vinculado desaparecer, a aplicação limpa automaticamente o vínculo no banco de dados. Desvincular um arquivo ou excluir sua partida o mantém na biblioteca; a exclusão explícita por um administrador remove o arquivo e todos os seus vínculos.

### Consultar relatórios

Abra `/reports` para escolher ano, trimestre, mês, Todo o período ou intervalo personalizado. A tabela mostra apenas jogadores com partidas válidas no período e permite abrir o desempenho contra cada oponente. Ela também mostra agregados por país e clube do oponente. Os links CSV e PDF preservam os filtros selecionados e usam os mesmos totais visíveis na tela; o nome do PDF inclui o jogador e o período.

### Relatar resultados

Use `Criar conta` na tela de login e peça a um administrador para vincular a conta a um jogador em `/admin/users`. O formulário só aceita partidas que incluam esse jogador vinculado. Os envios permanecem pendentes e não afetam classificações, ratings ou relatórios até serem aprovados por um diretor, operador ou administrador em `/admin/result-submissions`. O esquema e os helpers de serviço já incluem códigos hash expiráveis e de uso único para um futuro fluxo de aprovação por e-mail; a publicação automática por código permanece desativada até que a política de verificação seja definida.

Quando resultados de rodada são materializados na tabela principal de partidas, a coluna `event` preserva o nome do torneio ou evento. A coluna `notes`, exibida como `Round` na interface, armazena o número da rodada em formato canônico como inteiro simples, por exemplo `5` (e não `Round 5`). Se a entrada estiver em formato legado, como `15:00:00`, ela é preservada e convertida em rodada numérica. Se nenhum valor numérico for encontrado, o texto é mantido e tratado como `0`.

Tabelas de torneios são migradas automaticamente na inicialização para manter compatibilidade com bancos existentes.

Ao recalcular ratings, a ordem das rodadas é respeitada dentro de cada dia, tanto no recálculo completo quanto na atualização incremental. Se a rodada não puder ser determinada, usa-se a rodada 1.

### Revisar a auditoria administrativa

1. Entre em `/admin/login`.
2. Abra o painel administrativo e use a opção de auditoria.
3. Filtre por usuário ou ação para revisar alterações em jogadores, partidas, ratings, importações, usuários e configurações.

A página de auditoria mantém o histórico de atividades de cada conta e ajuda a verificar quem realizou cada alteração antes de ações de recuperação ou suporte.

O registro grava ações administrativas que alteram o estado: importações, ciclo de vida e resultados de torneios, alterações de jogadores e partidas, ratings e categorias, usuários e cópias de segurança. Ele mantém um resumo JSON compacto, limita os detalhes a 2 KiB por evento e remove entradas com mais de 730 dias por padrão. Defina `AUDIT_RETENTION_DAYS` antes da inicialização para escolher outro período positivo.

### Cópia de segurança e restauração

Use a tela administrativa de cópias de segurança antes de importações em massa, restaurações ou atualizações. O servidor gera e valida nomes de arquivos de backup, e bancos restaurados passam pela rota de migração da aplicação. A restauração também reconstrói o índice de busca de jogadores e considera apenas backups gerenciados pelo aplicativo ou o arquivo `.bak` designado; arquivos temporários em `data/` nunca são usados como fonte de restauração.

Cada cópia de segurança inclui um diretório lateral com a biblioteca SGF, e a restauração a recupera sem quebrar os vínculos.

## Desenvolvimento

Execute a suíte de regressão a partir da raiz do projeto:

```powershell
pytest -q
```

### Organização do código

As rotas administrativas estão separadas por domínio em `routes/admin_tournaments.py`, `routes/admin_matches.py`, `routes/admin_players.py` e `routes/admin_users.py`. A lógica de torneios está separada por responsabilidade em `services/tournament_gotha.py`, `services/tournament_participants.py`, `services/tournament_pairing.py`, `services/tournament_matches.py` e `services/tournament_standings.py`. `services/tournament_service.py` permanece como fachada de compatibilidade para os imports existentes. A suíte completa de regressão passa atualmente em 370 testes.

### Instalação em hospedagem Linux

Use Python 3.10 ou posterior e crie um ambiente virtual novo antes da instalação:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=Pillow -r requirements.txt
```

Se esse comando indicar que não existe uma wheel compatível do Pillow, a versão do
Python, a arquitetura ou a distribuição Linux escolhida pela hospedagem não é compatível.
Selecione Python 3.10+ x86_64 no painel da hospedagem; não compile o Pillow sem as
bibliotecas de desenvolvimento do sistema para Python, JPEG, zlib e freetype.

Os testes cobrem ratings e gráficos, filtros de jogadores, suporte a idiomas, backups, migrações de torneios, emparelhamento, classificação, compatibilidade com OpenGotha, moderação de resultados e páginas públicas de torneios.

A cobertura de testes também inclui a biblioteca SGF, a sincronização de seus metadados, a reparação de vínculos ausentes, suas permissões e a restauração a partir de cópias de segurança.

A ordenação, os filtros e a busca consistentes já estão entregues e validados nas páginas de jogadores, partidas e torneios.

## Licença e atribuição

Glicko DB foi originalmente desenvolvido para a comunidade de Go na Colômbia por Juan Felipe Samper em 2026.

O sistema [Glicko-2](https://www.glicko.net/glicko/glicko2.pdf) foi publicado por Mark Glickman em 2022 no domínio público. A implementação em Python é ©2009 Ryan Kirkman e o BesoGo é ©2015-2018 Ye Wang. Ambos são distribuídos sob a [licença MIT](static/vendor/besogo/LICENSE).
