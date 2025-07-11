from solana.rpc.api import Client
from solders.keypair import Keypair 
from solders.pubkey import Pubkey
import os
import logging
import yaml
from typing import Any
from pathlib import Path
from dotenv import load_dotenv

# Configure logging
logger = logging.getLogger(__name__)

class Config:
    """Configuration manager for the application"""
    
    def __init__(self):
        self._config = {}
        self._load_env()
        self._load_yaml()
        self._validate_config()

    def _load_env(self):
        """Load environment variables"""
        load_dotenv(override=True)
        
        # Required environment variables
        required_vars = [
            'HELIUS_API_KEY',
            'ACC_PRIVATE_KEY'
        ]
        
        # Validate and load required variables
        for var in required_vars:
            value = os.getenv(var)
            if not value:
                raise ValueError(f"Missing required environment variable: {var}")
            
        # Load environment-specific configuration
        self._config['env'] = {
            'helius': {
                'api_key': os.getenv('HELIUS_API_KEY'),
                'ws_url': f"wss://atlas-mainnet.helius-rpc.com/?api-key={os.getenv('HELIUS_API_KEY')}",
                'rpc_url': f"https://mainnet.helius-rpc.com/?api-key={os.getenv('HELIUS_API_KEY')}",
                'staked_rpc_url': f"https://staked.helius-rpc.com?api-key={os.getenv('HELIUS_API_KEY')}"
            },
            'acc_private_key': os.getenv('ACC_PRIVATE_KEY')
        }

    def _load_yaml(self):
        """Load YAML configuration"""
        config_path = Path(__file__).parent / 'config.yaml'
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
            
        with open(config_path) as f:
            self._config.update(yaml.safe_load(f))
            
    def _validate_config(self):
        """Validate configuration"""
        required_keys = ['helius', 'solana', 'tokens', 'constants']
        for key in required_keys:
            if key not in self._config:
                raise ValueError(f"Missing required configuration key: {key}")
                
        # Validate Helius configuration
        helius_config = self._config.get('helius', {})
        if 'rpc_url' not in helius_config:
            raise ValueError("Missing Helius RPC URL configuration")
            
        # Validate Solana configuration
        solana_config = self._config.get('solana', {})
        if 'unit_budget' not in solana_config or 'unit_price' not in solana_config:
            raise ValueError("Missing Solana unit budget or price configuration")
            
        # No need to set private key again as it's already in env section

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key"""
        keys = key.split('.')
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value
    
    def get_payer_keypair(self) -> Keypair:
        """Get wallet keypair"""
        try:
            private_key = self._config['env']['acc_private_key'].strip()
          
            if private_key.startswith('[') and private_key.endswith(']'):
                key_array = [int(x.strip()) for x in private_key[1:-1].split(',')]
                keypair = Keypair.from_bytes(bytes(key_array))
            else:
                # Try decoding from base64 first if it looks like base64
                if all(c in '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+/=' for c in private_key):
                    import base64
                    try:
                        decoded = base64.b64decode(private_key)
                        keypair = Keypair.from_bytes(decoded)
                    except:
                        # If base64 fails, try base58
                        keypair = Keypair.from_base58_string(private_key)
                else:
                    keypair = Keypair.from_base58_string(private_key)
                    
            logger.info(f"Successfully loaded payer keypair with public key: {keypair.pubkey()}")
            return keypair
        except Exception as e:
            logger.error(f"Failed to load payer keypair: {e}")
            raise
    
    def get_solana_rpc_client(self) -> Client:
        """Get RPC client"""
        return Client(self._config['env']['helius']['rpc_url'])

    def get_solana_rpc_url(self) -> str:
        """Get RPC URL string"""
        return self._config['env']['helius']['rpc_url']

    # Constants getters
    @property
    def WSOL(self) -> Pubkey:
        """Get WSOL address"""
        return Pubkey.from_string(self._config['tokens']['wsol']['address'])
    
    @property
    def SOL_DECIMAL(self) -> int:
        """Get SOL decimal"""
        return self._config['tokens']['wsol']['decimal']

    @property
    def UNIT_BUDGET(self) -> int:
        """Get unit budget"""
        return self._config['solana']['unit_budget']

    @property
    def UNIT_PRICE(self) -> int:
        """Get unit price"""
        return self._config['solana']['unit_price']

    @property
    def PUMP_FUN_PROGRAM(self) -> Pubkey:
        """Get PumpFun program address"""
        return Pubkey.from_string(self._config['constants']['pump_fun_program'])
    

    @property
    def GLOBAL(self) -> Pubkey:
        """Get GLOBAL address"""
        return Pubkey.from_string(self._config['constants']['global'])
    
    @property
    def FEE_RECIPIENT(self) -> Pubkey:
        """Get FEE_RECIPIENT address"""
        return Pubkey.from_string(self._config['constants']['fee_recipient'])
    
    @property
    def SYSTEM_PROGRAM(self) -> Pubkey:
        """Get SYSTEM_PROGRAM address"""
        return Pubkey.from_string(self._config['constants']['system_program'])
    
    @property
    def TOKEN_PROGRAM(self) -> Pubkey:
        """Get TOKEN_PROGRAM address"""
        return Pubkey.from_string(self._config['constants']['token_program'])
    
    @property
    def ASSOC_TOKEN_ACC_PROG(self) -> Pubkey:
        """Get ASSOC_TOKEN_ACC_PROG address"""
        return Pubkey.from_string(self._config['constants']['assoc_token_acc_prog'])
    
    @property
    def EVENT_AUTHORITY(self) -> Pubkey:
        """Get EVENT_AUTHORITY address"""
        return Pubkey.from_string(self._config['constants']['event_authority'])

# Create a singleton instance
config = Config()
