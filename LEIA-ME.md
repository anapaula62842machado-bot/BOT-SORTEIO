# Bot de inscrição e sorteio

Bot simples de Discord em Python para publicar uma inscrição, controlar participantes e sortear vencedores com confirmação manual.

## O que ele faz

- `/painel-inscricao`: publica em um canal escolhido. Você define título, descrição, prazo em horas, limite de participantes e uma imagem opcional.
- O botão **Participar** mostra o convite da org Fusion e pede confirmação. A inscrição só é registrada depois de a pessoa confirmar; vagas e prazo são conferidos novamente nesse momento.
- `/painel-dono`: abre um painel privado para ver a lista, fechar/reabrir inscrições, remover uma pessoa por ID, sortear vencedores e confirmar o resultado.
- O sorteio escolhe pessoas sem repetição. O resultado só vai ao canal e às DMs depois de você confirmar.
- `/enviar-na-dm`: selecione uma pessoa do servidor; o modal abre para escrever e enviar a mensagem.
- Inscrições são salvas em SQLite e o painel público continua com os botões após reiniciar o bot.

## Requisitos e configuração

1. Instale Python 3.10 ou mais recente.
2. Crie uma aplicação e um bot no [Discord Developer Portal](https://discord.com/developers/applications), copie o token e convide o bot para o servidor com permissões de enviar mensagens, incorporar links e usar comandos de aplicação.
3. Copie `.env.example` para `.env` e substitua `DISCORD_TOKEN` pelo token secreto. Não compartilhe esse arquivo.
4. No terminal, dentro desta pasta, instale as dependências e inicie:

   ```powershell
   py -m pip install -r requirements.txt
   py bot.py
   ```

5. No Discord, use `/painel-inscricao` para criar a primeira fila.

O comando `/painel-dono` e os controles são restritos ao dono do servidor Discord. Para testar os comandos rapidamente, permita que o bot veja e envie mensagens no canal escolhido. O bot precisa permanecer ligado para receber cliques e fechar inscrições no horário; em hospedagem gratuita, confira se o serviço não suspende processos inativos.

## Limites conhecidos

- O bot registra a confirmação da pessoa de que entrou na Fusion; não verifica automaticamente a participação na org.
- Mensagens privadas podem falhar se a pessoa bloquear DMs de membros/bots. O painel informa quando não consegue entregar.
- O prazo é definido em horas a partir da criação do painel. O limite máximo é 720 horas (30 dias).
- A lista pode ser longa; o painel mostra até o limite de uma mensagem do Discord por vez.
- O sorteio usa aleatoriedade segura do Python, mas não é um sistema de auditoria pública independente.
