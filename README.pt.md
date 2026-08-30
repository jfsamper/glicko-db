# Glicko DB

Documentação: [Español](README.md) | [English](README.en.md) | Português

Glicko DB é uma aplicação Flask e SQLite para gerenciar jogadores, ratings, partidas e torneios de um comunidade de Go. Ela oferece classificações e estatísticas públicas, além de telas administrativas protegidas para importações, configuração de rating, cópias de segurança e operações de torneios.

## Recursos

- Classificações públicas, busca de jogadores, perfis, histórico de partidas, gráficos de rating e conversão de categoria
- Cálculo Glicko-2 com parâmetros configuráveis de rating e categoria
- Interface pública em espanhol, inglês e português
- Administração de jogadores e partidas com paginação, filtros e ordenação consistente
- Importação de planilhas Excel, XML do OpenGotha e CSV legado de partidas
- Criação e edição de torneios, importação do OpenGotha, emparelhamentos, registro de resultados, classificação e exportação
- Relatórios públicos por período, iniciando por Todo o período, com filtros por jogador, exportação CSV/PDF traduzida, mudanças de rating e desempenho por oponente, país e clube
- Sistemas suíço, suíço por categoria, suíço acelerado e McMahon
- Tratamento de BYE e ausências, cópias de segurança, proteção de restauração e migrações SQLite
- Torneios em rascunho ocultos das listas públicas, com opção administrativa para mostrar rascunhos
- Partidas com handicap em pedras (estilo Go), com sugestão automática pela diferença de categoria e ajuste de rating no estilo OGS

## Requisitos

- Python 3.10 ou superior
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

Abra `http://127.0.0.1:5000`. O banco SQLite é criado em `data/acg_ratings.db` na primeira inicialização. Defina `LOAD_SAMPLE_DATA=1` somente para dados de exemplo locais; isso substitui o conjunto atual quando `rank-final.xlsx` existe.

## Configuração

Os valores padrão estão em `config.py`.

- `APP_SECRET_KEY`: chave de assinatura da sessão Flask; configure-a em produção.
- `ADMIN_PASSWORD`: senha atual de administrador; substitua o valor de desenvolvimento em produção.
- `LOAD_SAMPLE_DATA=1`: importação de exemplo para desenvolvimento local.
- `DB_PATH`: caminho do banco SQLite em `config.py`.
- `AUDIT_RETENTION_DAYS`: número de dias para manter eventos de auditoria; o padrão é `730`.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS` e `MAIL_FROM`: configurações SMTP para recuperação de senha; `PASSWORD_RESET_TTL_SECONDS` controla a expiração do link e o padrão é 3600 segundos.

As datas e horas geradas pelo aplicativo usam UTC-5 por padrão. Cada conta pode escolher um fuso horário IANA no gerenciamento de usuários; contas sem preferência continuam usando UTC-5. O Python usa o banco de dados IANA do sistema no Linux, enquanto `tzdata` fornece o fallback portátil no Windows ou em imagens Linux mínimas. Se o fuso salvo de uma conta não puder ser carregado, a exibição volta para UTC-05:00. No cálculo dos ratings, as partidas do mesmo dia são processadas pelo número da rodada e depois pela ordem de inserção; rodadas desconhecidas são tratadas como a rodada 1.

Os relatórios em `/reports` usam limites inclusivos `start_date` e `end_date`, e a inclusão no período usa o fuso horário fixo do servidor (UTC-5 por padrão), não o fuso da conta. A visão geral e os relatórios filtrados por jogador começam em Todo o período. A porcentagem de vitórias é vitórias divididas por partidas. Cada linha mostra pontos absolutos, variação percentual do rating e mudança inteira de categoria. O seletor de jogadores é ordenado pelo total de partidas. Os totais são calculados uma vez no servidor e reutilizados na página e nas exportações CSV/PDF; os rótulos e textos do PDF usam o idioma atual, enquanto registros com datas ou resultados inválidos são excluídos e contabilizados. Partidas materializadas de torneios mantêm uma identidade única por emparelhamento para impedir importações ou contagens duplicadas.

A administração usa contas nominadas com três funções: `administrator`, `tournament_director` e `operator`. Quando não existe nenhuma conta, o aplicativo cria um administrador inicial a partir de `ADMIN_PASSWORD` na primeira inicialização; contas adicionais e seus fusos horários são gerenciados em `/admin/users`. Cada usuário pode abrir `/admin/profile` para salvar idioma, tema, fuso horário, e-mail e senha. O link de recuperação em `/admin/login` usa tokens de uso único e respostas que não revelam se um e-mail existe; configure SMTP em produção. Tentativas com falha sofrem limitação; em produção, use HTTPS e senhas fortes e exclusivas. A autorização real se baseia na sessão do usuário e nas permissões. Apenas `administrator` e `operator` podem modificar jogadores, ratings e categorias; `tournament_director` mantém as operações de torneios.
Administradores podem ajustar o número máximo de tentativas de login, a janela de limitação e a duração do link de recuperação em `/admin/settings`. Esses valores são armazenados no SQLite, e o botão de restauração recupera os valores iniciais de `config.py`. `ADMIN_PASSWORD`, caminhos e credenciais SMTP continuam sendo configuração do ambiente.

## Plano do projeto

O plano detalhado e priorizado está em [FUTURE_FEATURES.md](FUTURE_FEATURES.md). A reconciliação explícita da importação, os payloads tipados do OpenGotha, a revisão administrativa por conta com busca livre e filtros por data, a melhoria do perfil do jogador e o modal explícito para excluir torneios estão implementados e verificados. Os perfis incluem atividade recente, sequências, histórico de torneios e filtro de temporada.

## Operações

### Importar ratings e partidas

1. Entre em `/admin/login`.
2. Abra a tela de importação.
3. Envie um dos formatos compatíveis:

  - `.xlsx` ou `.xls`: importa os dados e substitui o conjunto atual.
  - `.xml` do OpenGotha: importa partidas e metadados do torneio. O atributo `handicap` de cada partida (número de pedras dadas a Preto) é preservado quando presente.
  - `.csv`: requer as colunas `date`, `white`, `black` e `result`. Uma coluna opcional `handicap` (número de pedras, 0-9) é preservada; valores ausentes ou inválidos usam 0.
4. Confirme as classificações e os perfis de jogadores resultantes.

Mantenha uma cópia de segurança antes de importar uma planilha que substitua os dados.

### Realizar um torneio

1. Na administração, crie um torneio ou importe um arquivo XML do OpenGotha.
2. Adicione participantes e escolha suíço, suíço por categoria, suíço acelerado ou McMahon.
3. Gere ou administre manualmente os emparelhamentos de cada rodada.
4. Na tela do torneio, edite nome, local, número de rodadas, pontos de BYE e pontos de ausência.
5. Registre resultados clicando no nome do jogador vencedor ou no texto do resultado. O texto percorre `-`, `1-0`, `1/2-1/2` e `0-1`; clicar novamente no vencedor selecionado limpa o resultado. O vencedor aparece destacado em negrito e verde.
6. Registre BYEs e ausências, gere a rodada seguinte, revise a classificação e exporte os resultados com os botões administrativos.

As posições da classificação são sempre únicas e sequenciais; os empates são resolvidos por SOS, SOSOS, SODOS, rating e nome. O emparelhamento evita repetir o BYE para um jogador enquanto outro participante ainda não o recebeu, e os BYEs importados do OpenGotha são registrados para que as rodadas futuras respeitem esse histórico.

Quando uma importação do OpenGotha encontra um nome semelhante, ela mostra a sugestão de um jogador do banco de dados. Clique no nome sugerido para vinculá-lo imediatamente ao jogador existente ou use o seletor para criar um novo jogador ou escolher outro jogador.

Cada emparelhamento recebe uma sugestão automática de handicap em pedras (uma pedra por categoria de diferença entre os jogadores), que o diretor do torneio pode editar antes de registrar o resultado. Ao processar a rodada, o handicap é transferido para a partida e ajusta o rating no estilo OGS: o rating do adversário é deslocado apenas para o cálculo dessa partida, sem alterar seu rating base.

### Consultar relatórios

Abra `/reports` para escolher ano, trimestre, mês, Todo o período ou período personalizado. A tabela mostra jogadores com partidas válidas no período e permite abrir o desempenho contra cada oponente. Ela também mostra agregados por país e clube do oponente. Os links CSV e PDF preservam os filtros escolhidos e usam os mesmos totais exibidos na tela; o nome do PDF inclui o jogador e o período.

Quando os resultados da rodada são materializados na tabela principal de partidas, a coluna `event` mantém o nome do torneio ou evento. O campo `notes`, exibido como `Round` na interface, armazena o número da rodada em formato canônico como um inteiro sem rótulo, por exemplo `5` (não `Round 5`). Se a entrada estiver em um formato legado, como `15:00:00`, ela é preservada e convertida em uma rodada numérica. Se nenhum valor numérico for encontrado, o texto é mantido e tratado como `0`.

As tabelas de torneios são migradas automaticamente na inicialização para manter a compatibilidade com bancos existentes.

Ao recalcular ratings, a ordem das rodadas é respeitada dentro de cada dia, tanto no recálculo completo quanto na atualização incremental. Quando não é possível determinar a rodada, usa-se a rodada 1.

### Revisar a auditoria administrativa

1. Entre em `/admin/login`.
2. Abra o painel administrativo e use a opção de auditoria.
3. Filtre por usuário ou ação para revisar alterações em jogadores, partidas, ratings, importações, usuários e configurações.

A página de auditoria mantém o histórico de atividades de cada conta e ajuda a verificar quem realizou cada alteração antes de ações de recuperação ou suporte.

O registro grava as ações administrativas que alteram o estado: importações, ciclo de vida e resultados de torneios, alterações de jogadores e partidas, ratings e categorias, usuários e cópias de segurança. Ele mantém um resumo JSON compacto, limita os detalhes a 2 KiB por evento e remove por padrão entradas com mais de 730 dias. Defina `AUDIT_RETENTION_DAYS` antes da inicialização para escolher outro período positivo.

### Cópia de segurança e restauração

Use a tela administrativa de cópias de segurança antes de importações em massa, restaurações ou atualizações. O servidor gera e valida os nomes dos arquivos de backup, e os bancos restaurados passam pela rota de migração da aplicação. A restauração também reconstrói o índice de busca de jogadores e considera apenas backups gerenciados pelo aplicativo ou o arquivo `.bak` designado; arquivos temporários em `data/` nunca são usados como fonte de restauração.

## Desenvolvimento

Execute a suíte de regressão:

```powershell
pytest -q
```

### Instalação em hospedagem Linux

Use Python 3.10 ou posterior e crie um ambiente virtual novo antes da instalação:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=Pillow -r requirements.txt
```

Se esse comando informar que não existe uma wheel compatível do Pillow, a versão do
Python, a arquitetura ou a distribuição Linux escolhida pela hospedagem não é compatível.
Selecione Python 3.10+ x86_64 no painel da hospedagem; não compile o Pillow sem as
bibliotecas de desenvolvimento do sistema para Python, JPEG, zlib e freetype.

Os testes cobrem ratings e gráficos, filtros de jogadores, suporte a idiomas, backups, migrações de torneios, emparelhamento, classificação, compatibilidade com OpenGotha e páginas públicas de torneios.

A ordenação, os filtros e a busca consistentes já estão entregues e validados nas páginas de jogadores, partidas e torneios.

## Próximos recursos recomendados

1. Melhorias de paginação. Mostrar o total de páginas, o contexto da página atual e uma seleção simples de resultados por página.
2. Perfil do jogador com resultados e estatísticas de torneios. Adicionar em cada ficha um resumo de torneios disputados, registro de vitórias/derrotas, resultados por evento, tabela de torneios recentes, sequências e porcentagens de desempenho, com filtros por categoria e temporada.
3. Backups programados com retenção e verificação de restauração. Mantê-los desativados por padrão e habilitá-los apenas quando existir uma política clara de retenção.
4. Fluxo opcional de moderação de resultados. Somente se for necessário para o processo do torneio.

## Licença e atribuição

Revise os arquivos-fonte e dependências para detalhes de licença. A implementação Glicko-2 foi desenvolvida originalmente por Ryan Kirkman e publicada sob a licença MIT.