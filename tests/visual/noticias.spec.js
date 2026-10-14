// Regressao visual da pagina de noticias (index.html). Mesmo esquema do
// painel.spec.js: arquivo local, rede toda interceptada.
//   - Supabase: painel_noticias devolve fixtures/noticias.json (gravado do
//     banco real, com uma fonte marcada com erro);
//   - Mega-Sena: ultimo.json do Resultado Mega devolve fixtures/mega.json
//     (gravado da API real, concurso acumulado);
//   - imagens das noticias: um retangulo cinza;
//   - qualquer outro pedido: abortado.
// O relogio fica parado para "Hoje"/"Ontem" e as horas nao mudarem.
const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

const RAIZ = path.resolve(__dirname, "../..");
const PAGINA = "file://" + path.join(RAIZ, "index.html");
const DADOS = fs.readFileSync(path.join(__dirname, "fixtures/noticias.json"), "utf8");
const MEGA = fs.readFileSync(path.join(__dirname, "fixtures/mega.json"), "utf8");
const AGORA = new Date("2026-09-30T17:00:00Z");
const IMAGEM = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="250"><rect width="400" height="250" fill="#9a9890"/></svg>`;

async function abrir(page, { dados = "ok", mega = "ok", hash = "" } = {}) {
  await page.clock.setFixedTime(AGORA);
  // A Mega-Sena so responde depois do Supabase: as duas redesenham a lista e,
  // em ordem livre, o antialiasing de um canto variava entre execucoes.
  let supabaseRespondeu;
  const depoisDoSupabase = new Promise(r => { supabaseRespondeu = r; });
  await page.route("**/*", async rota => {
    const pedido = rota.request();
    if (pedido.url().startsWith("file:")) return rota.continue();
    if (pedido.url().includes("/rest/v1/rpc/painel_noticias")) {
      await (dados === "ok"
        ? rota.fulfill({ contentType: "application/json", body: DADOS })
        : rota.fulfill({ status: 503, contentType: "application/json", body: '{"message":"fora do ar"}' }));
      return supabaseRespondeu();
    }
    if (pedido.url() === "https://resultadomega.com.br/api/ultimo.json") {
      await depoisDoSupabase;
      return mega === "ok"
        ? rota.fulfill({ contentType: "application/json", body: MEGA })
        : rota.fulfill({ status: 503, contentType: "application/json", body: '{"erro":"fora do ar"}' });
    }
    if (pedido.resourceType() === "image") return rota.fulfill({ contentType: "image/svg+xml", body: IMAGEM });
    return rota.abort();
  });
  await page.goto(PAGINA + hash);
  if (dados === "ok") await expect(page.locator(".item:not(.fixa)").first()).toBeVisible();
  else await expect(page.locator("#aviso")).toBeVisible();
  if (mega === "ok") await expect(page.locator(".fixa")).toBeVisible();
  await page.waitForLoadState("networkidle");
  // As imagens visiveis precisam ter terminado de desenhar.
  await page.waitForFunction(() => [...document.images]
    .filter(img => { const r = img.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; })
    .every(img => img.complete));
}

async function semVazamento(page) {
  const vazamento = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  expect(vazamento, "largura que sobra alem da tela").toBeLessThanOrEqual(0);
}

test("noticias: lista", async ({ page }) => {
  await abrir(page);
  await semVazamento(page);
  await expect(page).toHaveScreenshot("noticias-lista.png");
});

// O resultado da Mega-Sena fica fixo em cima da lista, antes do primeiro dia,
// com qualquer filtro; se a API cair, a lista segue sem ele.
test("noticias: mega-sena fixa no topo", async ({ page }) => {
  await abrir(page);
  const fixa = page.locator("#lista > :first-child");
  await expect(fixa).toHaveClass(/fixa/);
  await expect(fixa.locator("h2")).toHaveText("Mega-Sena 3069: acumulou, próximo prêmio estimado em R$\u00a0150\u00a0milhões");
  expect(await fixa.locator(".dezenas span").allTextContents()).toEqual(["05", "19", "22", "44", "47", "57"]);
  await expect(fixa.locator(".meta")).toHaveText("Resultado Mega · Mega-Sena · sorteio de domingo,\u00a011\u00a0de\u00a0outubro");
  await page.getByRole("button", { name: /Ciência/ }).click();
  await expect(page.locator("#lista > :first-child")).toHaveClass(/fixa/);
});

test("noticias: sem mega-sena quando a API falha", async ({ page }) => {
  await abrir(page, { mega: "erro" });
  await expect(page.locator(".fixa")).toHaveCount(0);
  await expect(page.locator("#aviso")).toBeHidden();
  await expect(page).toHaveScreenshot("noticias-sem-mega.png");
});

test("noticias: filtro por tema", async ({ page }) => {
  await abrir(page);
  await page.getByRole("button", { name: /Pokémon TCG/ }).click();
  await expect(page.locator(".item:not(.fixa) .tema").first()).toHaveText("Pokémon TCG");
  expect(await page.locator(".item:not(.fixa) .tema").allTextContents()).toEqual(Array(6).fill("Pokémon TCG"));
  await expect(page).toHaveURL(/#pokemon-tcg$/);
});

// A captura vem da pagina aberta ja com o tema no endereco. Depois do clique,
// o chip pressionado e redesenhado e os cantos variavam 1 tom entre execucoes
// (desde que a lista passou a ser redesenhada quando a Mega-Sena chega).
test("noticias: tema pelo endereco", async ({ page }) => {
  await abrir(page, { hash: "#pokemon-tcg" });
  await expect(page.getByRole("button", { name: /Pokémon TCG/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".item:not(.fixa) .tema").first()).toHaveText("Pokémon TCG");
  await expect(page).toHaveScreenshot("noticias-tema.png");
});

test("noticias: busca sem resultado", async ({ page }) => {
  await abrir(page);
  await page.getByRole("searchbox").fill("zzz nada disso");
  await expect(page.locator(".vazio")).toHaveText("Nenhuma notícia com esse filtro.");
  await expect(page).toHaveScreenshot("noticias-busca-vazia.png");
});

test("noticias: rodape com as fontes", async ({ page }) => {
  await abrir(page);
  await page.locator("#rodape").scrollIntoViewIfNeeded();
  await expect(page.locator("#fontes .falha")).toHaveCount(1);
  await expect(page.locator("#rodape")).toHaveScreenshot("noticias-rodape.png");
});

test("noticias: tela de 320 px", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await abrir(page);
  await semVazamento(page);
  await expect(page).toHaveScreenshot("noticias-320.png");
});

test("noticias: erro ao carregar", async ({ page }) => {
  await abrir(page, { dados: "erro" });
  await expect(page.locator("#aviso")).toContainText("HTTP 503");
  await expect(page).toHaveScreenshot("noticias-erro.png");
});
