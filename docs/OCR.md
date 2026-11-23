# OCR de desenvolvimento

O fluxo prioriza texto digital. Em regiões rasterizadas o modo padrão usa
Tesseract em português e o classificador local Paddle para orientação.
Sem Tesseract ou sem o idioma português, a leitura digital permanece disponível
e regiões que precisavam de OCR são sinalizadas para revisão. O detector e o
reconhecedor Paddle pesados exigem configuração explícita.

| Variável | Valores e padrão |
|---|---|
| CEUMA_OCR_MODO | adaptativo (padrão), tesseract (sem correções adaptativas), paddle (motor original) |
| CEUMA_OCR_ORIENTACAO | paddle (padrão dos modos leves), osd (Tesseract OSD experimental), pdf (orientação declarada no PDF) |
| CEUMA_OCR_PREAQUECER | 1 habilita a preparação de orientação ao abrir a janela. Ausente ou 0 mantém a preparação sob demanda. |
| CEUMA_OCR_FALLBACK_PADDLE | 1 habilita fallback explícito no adaptativo, sujeito ao orçamento. Ausente ou 0 mantém o motor pesado desabilitado. |
| CEUMA_OCR_SEGUNDOS_PAGINA | Segundos positivos e finitos. Padrão 15. |
| CEUMA_OCR_SEGUNDOS_DOCUMENTO | Segundos positivos e finitos por PDF; o prazo recomeça a cada documento do lote. Padrão 60. |
| CEUMA_OCR_RESERVA_PADDLE | Segundos positivos e finitos exigidos antes de iniciar inferência pesada. Padrão 35. |

A espera pela preparação é medida separadamente do orçamento de OCR e
permanece incluída na latência percebida. Inferências nativas já iniciadas
podem terminar depois do prazo. O subprocesso Tesseract usa timeout.

## Referência experimental

No corpus sintético ampliado, 20 páginas com orientação preparada levaram
11,56 s. A frio, 20 páginas levaram 21,98 s. Os 52 campos esperados foram
recuperados. O pico amostrado no lote preparado foi 474 MiB. Antes da promoção
do padrão, passaram 297 testes automatizados e 27 scripts de regressão.
A validação em computadores de 4 GB e com documentos reais permanece pendente.
