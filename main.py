import os
import json
import base64
import toml
import requests
import uuid

from solders.pubkey import Pubkey as PublicKey
from solders.keypair import Keypair
from solana.rpc.api import Client
from solders.transaction import VersionedTransaction, Transaction
from spl.token.instructions import (
    get_associated_token_address,
    create_associated_token_account,
)

# Load configuration from config.toml in this directory
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.toml")
BASE_URL = "http://168.119.187.186:5000"


def load_config(config_path=CONFIG_PATH):
    conf = toml.load(config_path)
    return {
        "rpc_url": conf["rpc"]["url"],
        "program_id": conf["program"]["id"],
        "mint_pubkey": conf["mint"]["pubkey"],
    }


def load_payer(path: str = None):
    """
    Load a Keypair from a JSON file containing the secret key array.
    """
    # Determine default path relative to this script file
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "keys", "full.json")
    with open(path, "r") as f:
        raw = json.load(f)
    secret_key = bytes(raw)
    # Attempt to use the solders Keypair.from_bytes method
    try:
        return Keypair.from_bytes(secret_key)
    except AttributeError:
        # Fallback: direct constructor if supported
        return Keypair(secret_key)


def fetch_unsigned(route, body):
    res = requests.post(f"{BASE_URL}/{route}", json=body)
    res.raise_for_status()
    data = res.json()
    tx_bytes = base64.b64decode(data["tx"])
    # Deserialize a versioned transaction from raw bytes
    return VersionedTransaction.from_bytes(tx_bytes)


def sign_and_broadcast(tx: VersionedTransaction, payer: Keypair) -> str:
    # Create a signed VersionedTransaction using the fetched message and signer
    signed_tx = VersionedTransaction(tx.message, [payer])
    # Serialize to bytes
    raw = bytes(signed_tx)
    signed_b64 = base64.b64encode(raw).decode("utf-8")
    res = requests.post(f"{BASE_URL}/broadcast", json={"tx": signed_b64})
    res.raise_for_status()
    return res.json().get("sig")


def create_user(user_id_b64: str, payer: Keypair) -> str:
    tx = fetch_unsigned(
        "create_user",
        {
            "payer_pubkey": str(payer.pubkey()),
            "user_id": user_id_b64,
        },
    )
    return sign_and_broadcast(tx, payer)


def mint_to_treasury(amount: int, payer: Keypair, mint_pubkey: str) -> str:
    tx = fetch_unsigned(
        "mint",
        {
            "payer_pubkey": str(payer.pubkey()),
            "mint_pubkey": mint_pubkey,
            "amount": amount,
        },
    )
    return sign_and_broadcast(tx, payer)


def transfer_from_treasury(
    user_id_b64: str,
    amount: int,
    payer: Keypair,
    mint_pubkey: str,
    treasury_account: str,
    user_token_account: str,
) -> str:
    tx = fetch_unsigned("transfer", {
        "payer_pubkey": str(payer.pubkey()),
        "mint_pubkey": mint_pubkey,
        "from_id": user_id_b64,
        "to_id": user_id_b64,
        "amount": amount,
        "from_token_account": treasury_account,
        "to_token_account": user_token_account,
    })
    return sign_and_broadcast(tx, payer)


def deposit_to_user(
    user_id_b64: str,
    amount: int,
    payer: Keypair,
    mint_pubkey: str,
    treasury_account: str,
    user_token_account: str,
) -> str:
    tx = fetch_unsigned("deposit", {
        "payer_pubkey": str(payer.pubkey()),
        "mint_pubkey": mint_pubkey,
        "user_id": user_id_b64,
        "amount": amount,
        "user_token_account": user_token_account,
    })
    return sign_and_broadcast(tx, payer)


def balance_user(user_id_b64: str):
    res = requests.post(
        f"{BASE_URL}/balance_user",
        json={"user_id": user_id_b64},
    )
    res.raise_for_status()
    return res.json()


def total_supply():
    res = requests.get(f"{BASE_URL}/total_supply")
    res.raise_for_status()
    return res.json()


def balance_treasury():
    res = requests.get(f"{BASE_URL}/balance_treasury")
    res.raise_for_status()
    return res.json()


def get_or_create_associated_token_account(
    client: Client,
    payer: Keypair,
    mint: PublicKey,
) -> PublicKey:
    ata = get_associated_token_address(payer.pubkey(), mint)
    resp = client.get_account_info(ata)
    # resp.value is None when no account exists
    if resp.value is None:
        tx = Transaction()
        tx.add(
            create_associated_token_account(
                payer.pubkey(),
                payer.pubkey(),
                mint,
            )
        )
        client.send_transaction(tx, payer)
    return ata


def main():
    cfg = load_config()
    payer = load_payer()

    print("Payer:", payer.pubkey())

    SAMPLE_USER_ID = uuid.uuid4().hex
    SAMPLE_MINT_AMOUNT = 1_000_000
    SAMPLE_TRANSFER_AMOUNT = 100_000

    user_id_bytes = SAMPLE_USER_ID.encode("utf-8").ljust(32, b"\0")
    user_id_b64 = base64.b64encode(user_id_bytes).decode("utf-8")

    print("Creating user …")
    print("CreateUser sig:", create_user(user_id_b64, payer))

    print("Minting …")
    print(
        "Mint sig:",
        mint_to_treasury(
            SAMPLE_MINT_AMOUNT,
            payer,
            cfg["mint_pubkey"],
        ),
    )

    # Parse base58-encoded pubkeys into soldering Pubkey objects
    mint_pk = PublicKey.from_string(cfg["mint_pubkey"])
    program_pk = PublicKey.from_string(cfg["program_id"])

    # Compute treasury PDA
    treasury_account, _ = PublicKey.find_program_address(
        [b"treasury", bytes(mint_pk)],
        program_pk,
    )

    client = Client(cfg["rpc_url"])
    ata = get_or_create_associated_token_account(client, payer, mint_pk)
    user_token_account = str(ata)

    print("Depositing …")
    print("Deposit sig:", deposit_to_user(
        user_id_b64,
        SAMPLE_TRANSFER_AMOUNT,
        payer,
        cfg["mint_pubkey"],
        str(treasury_account),
        user_token_account,
    ))

    print("Fetching on-chain balances…")
    user_bal = balance_user(user_id_b64)
    print(
        f"User balance: free={user_bal['free_balance']}, "
        f"frozen={user_bal['frozen_balance']}",
    )
    
    supply = total_supply()
    print(
        f"Total supply: {supply['amount']} (decimals={supply['decimals']})"
    )
    
    treas_bal = balance_treasury()
    print(
        f"Treasury balance: {treas_bal['amount']} "
        f"(decimals={treas_bal['decimals']})"
    )


if __name__ == "__main__":
    main()
