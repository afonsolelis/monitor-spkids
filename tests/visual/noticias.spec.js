// Regressao visual da pagina de noticias (index.html). Mesmo esquema do
// painel.spec.js: arquivo local, rede toda interceptada.
//   - Supabase: painel_noticias devolve fixtures/noticias.json (gravado do
//     banco real, com uma fonte marcada com erro);
//   - imagens das noticias: um retangulo cinza;
//   - qualquer outro pedido: abortado.
// O relogio fica parado para "Hoje"/"Ontem" e as horas nao mudarem.
const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

const RAIZ = path.resolve(__dirname, "../..");
const PAGINA = "file://" + path.join(RAIZ, "index.html");
const DADOS = fs.readFileSync(path.join(__dirname, "fixtures/noticias.json"), "utf8");
const AGORA = new Date("2026-09-30T17:00:00Z");
const IMAGEM = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="250"><rect width="400" height="250" fill="#9a9890"/></svg>`;

async function abrir(page, { dados = "ok" } = {}) {
  await page.clock.setFixedTime(AGORA);
  await page.route("**/*", rota => {
    const pedido = rota.request();
    if (pedido.url().startsWith("file:")) return rota.continue();
    if (pedido.url().includes("/rest/v1/rpc/painel_noticias")) {
      return dados === "ok"
        ? rota.fulfill({ contentType: "application/json", body: DADOS })
        : rota.fulfill({ status: 503, contentType: "application/json", body: '{"message":"fora do ar"}' });
    }
    if (pedido.resourceType() === "image") return rota.fulfill({ contentType: "image/svg+xml", body: IMAGEM });
    return rota.abort();
  });
  await page.goto(PAGINA);
  if (dados === "ok") await expect(page.locator(".item").first()).toBeVisible();
  else await expect(page.locator("#aviso")).toBeVisible();
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

test("noticias: filtro por tema", async ({ page }) => {
  await abrir(page);
  await page.getByRole("button", { name: /Pokémon TCG/ }).click();
  await expect(page.locator(".item .tema").first()).toHaveText("Pokémon TCG");
  expect(await page.locator(".item .tema").allTextContents()).toEqual(Array(6).fill("Pokémon TCG"));
  await expect(page).toHaveURL(/#pokemon-tcg$/);
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
