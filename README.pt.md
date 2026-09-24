# Glicko DB

Documentação: [Español](README.md) | [English](README.en.md) | Português

Glicko DB é uma aplicação Flask e SQLite para gerenciar jogadores, ratings, partidas e torneios de uma comunidade de Go. Ela oferece classificações e estatísticas públicas, além de telas administrativas protegidas para importações, configuração do rating, cópias de segurança e operações de torneios.

## Índice

- [Recursos](#recursos)
- [Ajuda do usuário](#ajuda-do-usuário)
- [Requisitos](#requisitos)
- [Execução local](#execução-local)
- [Configuração](#configuração)
- [Plano do projeto](#plano-do-projeto)
- [Desenvolvimento](#desenvolvimento)
  - [Organização do código](#organização-do-código)
  - [Instalação em hospedagem Linux](#instalação-em-hospedagem-linux)
- [Licença e atribuição](#licença-e-atribuição)

## Recursos

- Classificações públicas, busca de jogadores, perfis, histórico de partidas, gráficos de rating e conversão de categoria
- Cálculo Glicko-2 com parâmetros configuráveis de rating e categoria
- Interface pública em espanhol, inglês e português
- Administração de jogadores e partidas com paginação, filtros e ordenação consistente
- Biblioteca pública de registros SGF; contas autenticadas podem enviar arquivos sem vinculá-los, enquanto diretores de torneio, operadores e administradores podem vinculá-los ou desvinculá-los
- Importação de livros Excel (XLSX), OpenGotha XML e arquivos de partidas em CSV
- Criação e edição de torneios, importação de OpenGotha, emparelhamentos, registro de resultados, classificação e exportação
- Registro de contas de membros e envio de resultados individuais para aprovação administrativa
- Relatórios públicos por período (por padrão, todo o tempo) com filtros por jogador, exportação CSV/PDF localizada, mudanças de rating e desempenho por oponente, país e clube
- Notícias publicadas por administradores, com links rápidos para jogadores, torneios, partidas e registros SGF
- Sistemas suíço, suíço por categoria, suíço acelerado e McMahon
- Tratamento de BYE e ausências, cópias de segurança, proteções de restauração e migrações SQLite
- Torneios em rascunho ocultos das listagens públicas, com opção administrativa para mostrar rascunhos
- Partidas com handicap em pedras (estilo Go), com sugestão automática pela diferença de categoria e ajuste de rating no estilo OGS

## Ajuda do usuário

O guia da interface é a referência para as tarefas diárias. Ele também está disponível pelo botão `?` na barra superior ou em `/help?lang=pt`.

- [Ajuda da interface](docs/user_interface.pt.md)
- [Rotas e notas de integração](docs/api_endpoints.pt.md)

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

- O fuso padrão é UTC-5. Cada conta pode escolher um fuso IANA; partidas do mesmo dia são processadas por rodada e depois pela ordem de inserção.
- `/reports` usa intervalos inclusivos `start_date` e `end_date` no fuso fixo do servidor. A tela e as exportações CSV/PDF usam os mesmos filtros e totais.
- As contas têm as funções `administrator`, `tournament_director`, `operator` e `member`. Membros enviam apenas resultados do jogador vinculado; as outras funções revisam a fila de aprovação.
- `/admin/settings` permite ajustar limites de login e expiração da recuperação. Em produção, use HTTPS, senhas exclusivas e segredos apenas em variáveis de ambiente.
- Contas autenticadas podem enviar arquivos SGF sem vinculá-los. Administradores, diretores e operadores podem vinculá-los ou desvinculá-los; apenas administradores podem excluir arquivos.

## Plano do projeto

O plano está em [FUTURE_FEATURES.md](FUTURE_FEATURES.md). A reconciliação de importações, os payloads tipados do OpenGotha, os filtros de auditoria, as melhorias de perfil e a exclusão explícita de torneios já estão implementados. O trabalho restante inclui melhorar o tema escuro do BesoGo.

## Desenvolvimento

Regenere os guias HTML depois de alterar suas fontes Markdown:

```powershell
python scripts/build_help_html.py
```

Execute a suíte de regressão a partir da raiz do projeto:

```powershell
pytest -q
```

### Organização do código

As rotas administrativas estão separadas por domínio em `routes/admin_tournaments.py`, `routes/admin_matches.py`, `routes/admin_players.py` e `routes/admin_users.py`. A lógica de torneios está separada por responsabilidade em `services/tournament_gotha.py`, `services/tournament_participants.py`, `services/tournament_pairing.py`, `services/tournament_matches.py` e `services/tournament_standings.py`. As traduções e a seleção de idioma ficam em `services/i18n.py`, enquanto os helpers puros de gráficos de rating ficam em `services/chart_service.py`; `services/common.py` mantém exports de compatibilidade para os imports existentes. `services/tournament_service.py` permanece como fachada de compatibilidade. A suíte completa de regressão passa atualmente em 373 testes.

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
