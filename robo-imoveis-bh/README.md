# 🏠 Radar de Imóveis BH

Robô que fica procurando **casas e apartamentos para alugar em BH** (foco em Pampulha, Ouro Preto,
Trevo, Cidade Nova e região), dá uma **nota** para cada um de acordo com o seu gosto e mostra tudo
num **painel** que se atualiza sozinho. Opcionalmente, uma **IA olha as fotos** e diz se o imóvel é
bonito, moderno e arejado, e o robô **avisa no Telegram** quando aparece coisa boa.

## O que ele faz

1. A cada 30 minutos, varre as páginas públicas de busca do **QuintoAndar** e do **Chaves na Mão**
   (este último agrega anúncios de várias imobiliárias de BH).
2. Descarta o que não serve: menos de 3 quartos, sem varanda, apertado (< 80 m²), fora da região
   ou acima do orçamento. Tudo isso é configurável.
3. Dá uma nota de 0 a 100. Ganham pontos: cobertura, bairro favorito, área grande, 2+ banheiros,
   descrição com cara de moderno ("porcelanato", "planejados", "gourmet") e de arejado
   ("amplo", "iluminado", "vista livre"). Perdem pontos: "precisa de reforma", "fundos", "antigo".
4. **IA (opcional):** o Claude analisa as fotos e dá notas de beleza, modernidade e espaço/luz,
   além de um resumo sincero com pontos positivos e negativos.
5. Guarda o histórico: marca **NOVO**, **BAIXOU DE PREÇO** e **SAIU DO AR**.
6. No painel você favorita, descarta e abre o anúncio original.

## Como rodar (no seu computador)

Precisa de **Python 3.11 ou mais novo** ([python.org](https://www.python.org/downloads/); no
Windows, marque "Add Python to PATH" na instalação).

```bash
cd robo-imoveis-bh
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Abra **http://localhost:8000**. A primeira varredura leva uns 15 minutos (o robô espera alguns
segundos entre uma página e outra para não sobrecarregar os sites). Depois disso ele repete
sozinho a cada 30 minutos, enquanto o programa estiver aberto.

No Windows, também dá para clicar duas vezes em **`iniciar.bat`**.

- Ver no celular (mesmo Wi-Fi): `python run.py --rede` e abra `http://IP-DO-PC:8000`.
- Só o painel, sem varrer: `python run.py --so-painel`.
- Uma varredura só, sem painel: `python -m robo.coletor`.

## Ligar a IA das fotos (opcional, recomendado)

1. Crie uma chave em https://platform.claude.com (Settings → API Keys).
2. Antes de rodar, defina a variável de ambiente:
   - Windows (PowerShell): `$env:ANTHROPIC_API_KEY="sk-ant-..."`
   - Mac/Linux: `export ANTHROPIC_API_KEY="sk-ant-..."`
3. Rode `python run.py` normalmente.

O custo é baixo: são no máximo 15 imóveis avaliados por varredura (`max_avaliacoes_por_ciclo`), e
cada imóvel é avaliado uma única vez. O modelo usado fica em `[ia] modelo` no `config.toml`.
Sem a chave, tudo funciona igual, só sem a nota das fotos.

## Avisos no Telegram (opcional)

1. No Telegram, fale com **@BotFather**, mande `/newbot` e copie o token.
2. Mande qualquer mensagem para o seu bot novo e abra
   `https://api.telegram.org/bot<TOKEN>/getUpdates` para descobrir o seu `chat_id`.
3. Defina `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` (do mesmo jeito que a chave da IA) e mude
   `ativo = true` na seção `[telegram]` do `config.toml`.

Na primeira varredura ele manda só um resumo. Depois disso, avisa cada imóvel novo (ou que baixou
de preço) com nota a partir de `nota_minima`.

## Ajustar o gosto

Tudo fica no **`config.toml`**: faixa de preço, quartos, área mínima, varanda obrigatória,
bairros favoritos e aceitos, intervalo entre varreduras e o texto que descreve o seu gosto para a
IA. O robô relê o arquivo a cada varredura, então não precisa reiniciar.

## Limites (pra saber o que esperar)

- **VivaReal, ZAP, OLX, Imovelweb e Netimóveis** bloqueiam robôs (Cloudflare) e não estão
  incluídos. Para não perder nada, vale criar também um alerta de busca dentro desses sites.
- O QuintoAndar mostra 24 imóveis por busca e o Chaves na Mão, 30. Por isso o robô faz várias
  buscas menores (por bairro × tipo). Pode escapar um ou outro anúncio, mas os novos entram assim
  que aparecem.
- O robô só lê páginas públicas que os sites permitem no `robots.txt` e espera entre cada página.
  Use para busca pessoal.
- Os dados ficam em `dados/imoveis.db` (SQLite) e o registro de atividade em `dados/robo.log`.

## Estrutura

```
run.py                 liga o robô + painel
config.toml            suas preferências
robo/fontes/           leitores de cada site (quintoandar.py, chavesnamao.py)
robo/pontuacao.py      filtros e nota
robo/ia.py             avaliação das fotos com Claude
robo/coletor.py        uma varredura completa + agendamento
robo/web.py, static/   painel
```
