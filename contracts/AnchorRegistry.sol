// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title AnchorRegistry
/// @notice Minimal, gas-cheap registry for Foxy Audit chain-head anchors.
///         Each call records that `msg.sender` published hash-chain root `root`
///         at this block. The root is emitted in an event (cheap — event logs
///         cost far less than storage) so anyone can later fetch the anchoring
///         transaction / log and prove the ledger existed in that state at that
///         block. We deliberately store nothing: the calldata + event are the
///         permanent, publicly-verifiable record.
///
///         Deployment target for the pilot is an EVM testnet (Sepolia); the same
///         contract works on any EVM (an L2 or mainnet) with no code change.
contract AnchorRegistry {
    /// @param submitter the account that anchored (one per org key in practice)
    /// @param root       the 32-byte hash-chain head being anchored
    /// @param blockNumber the block this anchor landed in
    event Anchored(address indexed submitter, bytes32 indexed root, uint256 blockNumber);

    /// @notice Anchor a hash-chain root. Emits {Anchored}; stores nothing.
    /// @param root the org's chain head (audit_logs.chain_hash as bytes32)
    function anchor(bytes32 root) external {
        emit Anchored(msg.sender, root, block.number);
    }
}
