const fs = require("fs/promises");
const axios = require("axios");
const toml = require("toml");
const { PublicKey, Keypair, VersionedTransaction, Connection } = require("@solana/web3.js");
const { getOrCreateAssociatedTokenAccount } = require("@solana/spl-token");
const path = require("path");

const CONFIG_PATH = path.resolve(__dirname, "./config.toml");

async function loadConfig(configPath = CONFIG_PATH) {
  const conf = toml.parse(await fs.readFile(configPath, "utf8"));
  return {
    rpcUrl: conf.rpc.url,
    programId: conf.program.id,
    mintPubkey: conf.mint.pubkey,
  };
}

async function loadPayer(path = "keys/full.json") {
  const raw = JSON.parse(await fs.readFile(path, "utf8"));
  return Keypair.fromSecretKey(Uint8Array.from(raw));
}

const api = axios.create({ baseURL: "http://168.119.187.186:5000" });

async function fetchUnsigned(route, body) {
  const { data } = await api.post("/" + route, body);
  return VersionedTransaction.deserialize(Buffer.from(data.tx, "base64"));
}

async function signAndBroadcast(tx, payer) {
  tx.sign([payer]);
  const signedB64 = Buffer.from(tx.serialize()).toString("base64");
  const { data } = await api.post("/broadcast", { tx: signedB64 });
  return data.sig;
}

async function createUser(userIdB64, payer) {
  const sig = await signAndBroadcast(
    await fetchUnsigned("create_user", {
      payer_pubkey: payer.publicKey.toBase58(),
      user_id: userIdB64,
    }),
    payer,
  );
  return sig;
}

async function mintToTreasury(amount, payer, mintPubkey) {
  const sig = await signAndBroadcast(
    await fetchUnsigned("mint", {
      payer_pubkey: payer.publicKey.toBase58(),
      mint_pubkey: mintPubkey,
      amount,
    }),
    payer,
  );
  return sig;
}

async function transferFromTreasury(userIdB64, amount, payer, mintPubkey, treasuryAccount, userTokenAccount) {
  const sig = await signAndBroadcast(
    await fetchUnsigned("transfer", {
      payer_pubkey: payer.publicKey.toBase58(),
      mint_pubkey: mintPubkey,
      from_id: userIdB64,
      to_id: userIdB64,
      amount,
      from_token_account: treasuryAccount,
      to_token_account: userTokenAccount,
    }),
    payer,
  );
  return sig;
}

async function depositToUser(userIdB64, amount, payer, mintPubkey, treasuryAccount, userTokenAccount) {
  return await signAndBroadcast(
    await fetchUnsigned("deposit", {
      payer_pubkey: payer.publicKey.toBase58(),
      mint_pubkey: mintPubkey,
      user_id: userIdB64,
      amount,
      user_token_account: userTokenAccount,
    }),
    payer,
  );
}

async function balanceUser(userIdB64) {
  const res = await api.post("/balance_user", { user_id: userIdB64 });
  return res.data;
}

async function totalSupply() {
  const res = await api.get("/total_supply");
  return res.data;
}

async function balanceTreasury() {
  const res = await api.get("/balance_treasury");
  return res.data;
}

async function main() {
  const cfg = await loadConfig();
  const payer = await loadPayer();

  console.log("Payer:", payer.publicKey.toBase58());

  const SAMPLE_USER_ID = "example_user";
  const SAMPLE_MINT_AMOUNT = 1_000_000;
  const SAMPLE_TRANSFER_AMOUNT = 100_000;

  const userIdBytes = Buffer.from(SAMPLE_USER_ID.padEnd(32, "\0"));
  const userIdB64   = userIdBytes.toString("base64");

  console.log("Creating user …");
  console.log("CreateUser sig:", await createUser(userIdB64, payer));

  console.log("Minting …");
  console.log(
    "Mint sig:",
    await mintToTreasury(SAMPLE_MINT_AMOUNT, payer, cfg.mintPubkey),
  );

  const [treasuryAccount] = PublicKey.findProgramAddressSync(
    [Buffer.from("treasury"), (new PublicKey(cfg.mintPubkey)).toBuffer()],
    new PublicKey(cfg.programId)
  );
  const connection = new Connection(cfg.rpcUrl);
  const ata = await getOrCreateAssociatedTokenAccount(
    connection,
    payer,
    new PublicKey(cfg.mintPubkey),
    payer.publicKey
  );
  const userTokenAccount = ata.address;

  console.log("Depositing …");
  console.log(
    "Deposit sig:",
    await depositToUser(
      userIdB64,
      SAMPLE_TRANSFER_AMOUNT,
      payer,
      cfg.mintPubkey,
      treasuryAccount.toBase58(),
      userTokenAccount.toBase58(),
    ),
  );

  console.log("Fetching on‑chain balances…");
  const userBal = await balanceUser(userIdB64);
  console.log(`User balance: free=${userBal.free_balance}, frozen=${userBal.frozen_balance}`);

  const supply = await totalSupply();
  console.log(`Total supply: ${supply.amount} (decimals=${supply.decimals})`);

  const treasBal = await balanceTreasury();
  console.log(`Treasury balance: ${treasBal.amount} (decimals=${treasBal.decimals})`);

}

if (require.main === module) {
  main().catch(console.error);
}
