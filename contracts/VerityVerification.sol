// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title VerityVerification
 * @notice Smart contract for VERITY: Blockchain-Based Verification System for
 *         Digital Content Authenticity and Deepfake Detection.
 *         Dr. Vishwanath Karad MIT World Peace University, Pune (2026-27).
 *
 * Stores cryptographic content digests, perceptual fingerprint references,
 * canonical verification record hashes, and periodic off-chain ledger checkpoints.
 * Original media files and personal filenames are never stored on-chain.
 */
contract VerityVerification {
    struct VerificationRecord {
        bytes32 eventKey;
        bytes32 contentSha256;
        string perceptualFingerprint;
        string verificationId;
        string mediaType;
        string resultCategory;
        string analysisConfigId;
        bytes32 recordHash;
        uint256 localSequence;
        uint256 timestamp;
        address submitter;
        bool exists;
    }

    struct LedgerCheckpoint {
        uint256 checkpointId;
        bytes32 ledgerRootHash;
        uint256 recordCount;
        uint256 lastSequenceNumber;
        uint256 timestamp;
        address submitter;
        string note;
        bool exists;
    }

    address public owner;
    mapping(address => bool) public authorizedSubmitters;

    mapping(bytes32 => VerificationRecord) private _records;
    mapping(bytes32 => bytes32[]) private _contentToEventKeys;
    mapping(string => bytes32) private _verificationIdToKey;
    mapping(bytes32 => bytes32) private _recordHashToKey;
    bytes32[] private _allEventKeys;

    LedgerCheckpoint[] private _checkpoints;
    mapping(bytes32 => uint256) private _checkpointHashToId;
    bytes32 public latestAnchoredLedgerHash;
    uint256 public latestCheckpointRecordCount;

    error NotOwner();
    error NotAuthorized();
    error InvalidInput(string field);
    error DuplicateVerificationEvent(bytes32 eventKey);
    error RecordNotFound(bytes32 key);
    error CheckpointNotFound(uint256 checkpointId);

    event VerificationRegistered(
        bytes32 indexed eventKey,
        bytes32 indexed contentSha256,
        string verificationId,
        string mediaType,
        string resultCategory,
        string analysisConfigId,
        bytes32 recordHash,
        address indexed submitter,
        uint256 timestamp
    );

    event CheckpointRegistered(
        uint256 indexed checkpointId,
        bytes32 indexed ledgerRootHash,
        uint256 recordCount,
        uint256 lastSequenceNumber,
        address indexed submitter,
        uint256 timestamp
    );

    event SubmitterAuthorizationChanged(address indexed account, bool authorized);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyAuthorized() {
        if (msg.sender != owner && !authorizedSubmitters[msg.sender]) {
            revert NotAuthorized();
        }
        _;
    }

    constructor() {
        owner = msg.sender;
        authorizedSubmitters[msg.sender] = true;
        emit SubmitterAuthorizationChanged(msg.sender, true);
    }

    function setAuthorizedSubmitter(address account, bool authorized) external onlyOwner {
        if (account == address(0)) revert InvalidInput("account");
        authorizedSubmitters[account] = authorized;
        emit SubmitterAuthorizationChanged(account, authorized);
    }

    function computeEventKey(
        bytes32 contentSha256,
        string memory analysisConfigId
    ) public pure returns (bytes32) {
        return keccak256(abi.encode(contentSha256, analysisConfigId));
    }

    function registerVerification(
        bytes32 contentSha256,
        string calldata perceptualFingerprint,
        string calldata verificationId,
        string calldata mediaType,
        string calldata resultCategory,
        string calldata analysisConfigId,
        bytes32 recordHash,
        uint256 localSequence
    ) external onlyAuthorized returns (bytes32 eventKey) {
        if (contentSha256 == bytes32(0)) revert InvalidInput("contentSha256");
        if (recordHash == bytes32(0)) revert InvalidInput("recordHash");
        if (bytes(verificationId).length == 0) revert InvalidInput("verificationId");
        if (bytes(mediaType).length == 0) revert InvalidInput("mediaType");
        if (bytes(resultCategory).length == 0) revert InvalidInput("resultCategory");
        if (bytes(analysisConfigId).length == 0) revert InvalidInput("analysisConfigId");
        if (bytes(perceptualFingerprint).length == 0) revert InvalidInput("perceptualFingerprint");

        eventKey = computeEventKey(contentSha256, analysisConfigId);
        if (_records[eventKey].exists) {
            revert DuplicateVerificationEvent(eventKey);
        }
        if (_verificationIdToKey[verificationId] != bytes32(0)) {
            revert InvalidInput("verificationId");
        }

        _records[eventKey] = VerificationRecord({
            eventKey: eventKey,
            contentSha256: contentSha256,
            perceptualFingerprint: perceptualFingerprint,
            verificationId: verificationId,
            mediaType: mediaType,
            resultCategory: resultCategory,
            analysisConfigId: analysisConfigId,
            recordHash: recordHash,
            localSequence: localSequence,
            timestamp: block.timestamp,
            submitter: msg.sender,
            exists: true
        });

        _contentToEventKeys[contentSha256].push(eventKey);
        _verificationIdToKey[verificationId] = eventKey;
        _recordHashToKey[recordHash] = eventKey;
        _allEventKeys.push(eventKey);

        emit VerificationRegistered(
            eventKey,
            contentSha256,
            verificationId,
            mediaType,
            resultCategory,
            analysisConfigId,
            recordHash,
            msg.sender,
            block.timestamp
        );
    }

    function hasVerificationEvent(
        bytes32 contentSha256,
        string calldata analysisConfigId
    ) external view returns (bool) {
        bytes32 eventKey = computeEventKey(contentSha256, analysisConfigId);
        return _records[eventKey].exists;
    }

    function getVerificationEvent(
        bytes32 contentSha256,
        string calldata analysisConfigId
    ) external view returns (VerificationRecord memory) {
        bytes32 eventKey = computeEventKey(contentSha256, analysisConfigId);
        if (!_records[eventKey].exists) revert RecordNotFound(eventKey);
        return _records[eventKey];
    }

    function getVerificationByKey(bytes32 eventKey) external view returns (VerificationRecord memory) {
        if (!_records[eventKey].exists) revert RecordNotFound(eventKey);
        return _records[eventKey];
    }

    function getVerificationById(string calldata verificationId) external view returns (VerificationRecord memory) {
        bytes32 eventKey = _verificationIdToKey[verificationId];
        if (eventKey == bytes32(0) || !_records[eventKey].exists) {
            revert RecordNotFound(eventKey);
        }
        return _records[eventKey];
    }

    function verifyRecordHashOnChain(
        bytes32 recordHash
    )
        external
        view
        returns (bool exists, bytes32 eventKey, bytes32 contentSha256, uint256 timestamp)
    {
        eventKey = _recordHashToKey[recordHash];
        if (eventKey == bytes32(0) || !_records[eventKey].exists) {
            return (false, bytes32(0), bytes32(0), 0);
        }
        VerificationRecord storage rec = _records[eventKey];
        return (true, eventKey, rec.contentSha256, rec.timestamp);
    }

    function getContentEventKeys(bytes32 contentSha256) external view returns (bytes32[] memory) {
        return _contentToEventKeys[contentSha256];
    }

    function getRecordsByContentHash(bytes32 contentSha256) external view returns (VerificationRecord[] memory) {
        bytes32[] storage keys = _contentToEventKeys[contentSha256];
        VerificationRecord[] memory items = new VerificationRecord[](keys.length);
        for (uint256 i = 0; i < keys.length; i++) {
            items[i] = _records[keys[i]];
        }
        return items;
    }

    function getTotalRecords() external view returns (uint256) {
        return _allEventKeys.length;
    }

    function getEventKeysSlice(uint256 offset, uint256 limit) external view returns (bytes32[] memory) {
        uint256 total = _allEventKeys.length;
        if (offset >= total || limit == 0) {
            return new bytes32[](0);
        }
        uint256 end = offset + limit;
        if (end > total) {
            end = total;
        }
        uint256 size = end - offset;
        bytes32[] memory slice = new bytes32[](size);
        for (uint256 i = 0; i < size; i++) {
            slice[i] = _allEventKeys[offset + i];
        }
        return slice;
    }

    function registerCheckpoint(
        bytes32 ledgerRootHash,
        uint256 recordCount,
        uint256 lastSequenceNumber,
        string calldata note
    ) external onlyAuthorized returns (uint256 checkpointId) {
        if (ledgerRootHash == bytes32(0)) revert InvalidInput("ledgerRootHash");
        if (recordCount == 0) revert InvalidInput("recordCount");
        if (recordCount < latestCheckpointRecordCount) {
            revert InvalidInput("recordCount");
        }

        checkpointId = _checkpoints.length + 1;
        _checkpoints.push(
            LedgerCheckpoint({
                checkpointId: checkpointId,
                ledgerRootHash: ledgerRootHash,
                recordCount: recordCount,
                lastSequenceNumber: lastSequenceNumber,
                timestamp: block.timestamp,
                submitter: msg.sender,
                note: note,
                exists: true
            })
        );

        _checkpointHashToId[ledgerRootHash] = checkpointId;
        latestAnchoredLedgerHash = ledgerRootHash;
        latestCheckpointRecordCount = recordCount;

        emit CheckpointRegistered(
            checkpointId,
            ledgerRootHash,
            recordCount,
            lastSequenceNumber,
            msg.sender,
            block.timestamp
        );
    }

    function getCheckpointsCount() external view returns (uint256) {
        return _checkpoints.length;
    }

    function getCheckpoint(uint256 checkpointId) external view returns (LedgerCheckpoint memory) {
        if (checkpointId == 0 || checkpointId > _checkpoints.length) {
            revert CheckpointNotFound(checkpointId);
        }
        return _checkpoints[checkpointId - 1];
    }

    function getLatestCheckpoint() external view returns (LedgerCheckpoint memory) {
        if (_checkpoints.length == 0) {
            revert CheckpointNotFound(0);
        }
        return _checkpoints[_checkpoints.length - 1];
    }

    function verifyAnchoredLedgerHash(
        bytes32 ledgerRootHash
    )
        external
        view
        returns (bool matched, uint256 checkpointId, uint256 recordCount, uint256 timestamp)
    {
        checkpointId = _checkpointHashToId[ledgerRootHash];
        if (checkpointId == 0) {
            return (false, 0, 0, 0);
        }
        LedgerCheckpoint storage cp = _checkpoints[checkpointId - 1];
        return (true, cp.checkpointId, cp.recordCount, cp.timestamp);
    }
}
