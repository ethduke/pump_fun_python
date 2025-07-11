import json
import time
import logging
from solana.rpc.commitment import Processed, Confirmed
from solana.rpc.types import TokenAccountOpts
from solders.signature import Signature # type: ignore
from solders.pubkey import Pubkey  # type: ignore
from model.providers.solana_provider import SolanaProvider

# Configure logging
logger = logging.getLogger(__name__)

# Initialize Solana provider
solana_provider = SolanaProvider.get_instance()
client = solana_provider.rpc
payer_keypair = solana_provider.payer


def get_token_balance(pub_key: Pubkey, mint: Pubkey) -> float | None:
    try:
        response = client.get_token_accounts_by_owner_json_parsed(
            pub_key,
            TokenAccountOpts(mint=mint),
            commitment=Processed
        )

        accounts = response.value
        if accounts:
            token_amount = accounts[0].account.data.parsed['info']['tokenAmount']['uiAmount']
            return float(token_amount)

        return None
    except Exception as e:
        print(f"Error fetching token balance: {e}")
        return None

def confirm_txn(txn_sig: Signature, max_retries: int = 20, retry_interval: int = 3) -> bool:
    retries = 0  # Start at 0 instead of 3
    
    while retries < max_retries:
        try:
            txn_res = client.get_transaction(txn_sig, encoding="json", commitment=Confirmed, max_supported_transaction_version=0)
            
            # Check if transaction was found
            if txn_res.value is None:
                print(f"Transaction not found yet... try count: {retries}")
                retries += 1
                time.sleep(retry_interval)
                continue
                
            txn_json = json.loads(txn_res.value.transaction.meta.to_json())
            
            if txn_json['err'] is None:
                print(f"Transaction confirmed... try count: {retries}")
                return True
            else:
                print(f"Transaction failed with error: {txn_json['err']}")
                return False
                
        except Exception as e:
            print(f"Awaiting confirmation... try count: {retries} - {str(e)}")
            retries += 1
            time.sleep(retry_interval)
    
    print("Max retries reached. Transaction confirmation failed.")
    return False  # Return False instead of None
