#include <stdint.h>
#include <string.h>

/*
 * RandomX batch miner C extension — compatible with both rx/0 and rx/2.
 *
 * This extension calls the RandomX library's hash_first/hash_next functions
 * via function pointers passed from Python. The algorithm differences between
 * v1 and v2 are entirely handled inside the RandomX library — the VM is
 * configured with the correct flags (RANDOMX_FLAG_V2) before mining begins.
 *
 * v2 changes (384 instructions, AES register mixing, extended prefetch) are
 * transparent to this code since hash_first/hash_next API is unchanged.
 */

// Type definitions matching dataset_bindings.py
typedef void* rx_vm_t;

// Function pointers to the RandomX C library
typedef void (*rx_calc_first_t)(rx_vm_t vm, const void* input, size_t inputSize);
typedef void (*rx_calc_next_t)(rx_vm_t vm, const void* nextInput, size_t nextInputSize, void* output);
typedef void (*rx_calc_last_t)(rx_vm_t vm, void* output);

/**
 * Pipelined batched mining loop to avoid Python GIL overhead.
 * Works with both RandomX v1 (rx/0) and v2 (rx/2) — the VM determines
 * which algorithm variant is executed.
 * 
 * @param vm             RandomX VM instance (configured with appropriate flags)
 * @param blob           Monero block hashing blob (mutable)
 * @param blob_size      Size of the blob (usually 76 bytes for rx/0 and rx/2)
 * @param nonce_offset   Offset of the 4-byte nonce (usually 39)
 * @param start_nonce    Starting nonce for this thread
 * @param batch_size     Number of hashes to compute in this batch
 * @param step           Nonce increment (usually num_workers)
 * @param target         64-bit target threshold (matches xmrig)
 * @param out_nonce      OUT: Nonce of a matching share (if any)
 * @param out_hash       OUT: 32-byte hash result of the matching share (if any)
 * @param hash_first_fn  Pointer to randomx_calculate_hash_first
 * @param hash_next_fn   Pointer to randomx_calculate_hash_next
 * 
 * @return Number of shares found (0 or 1). 
 */
int rx_batch_mine(
    rx_vm_t vm,
    uint8_t* blob,
    size_t blob_size,
    int nonce_offset,
    uint32_t start_nonce,
    uint32_t batch_size,
    uint32_t step,
    uint64_t target,
    uint32_t* out_nonce,
    uint8_t* out_hash,
    rx_calc_first_t hash_first_fn,
    rx_calc_next_t hash_next_fn,
    rx_calc_last_t hash_last_fn
) {
    if (batch_size == 0) return 0;

    uint32_t nonce = start_nonce;
    uint8_t hash_result[32];
    uint32_t* nonce_ptr = (uint32_t*)(blob + nonce_offset);

    // Prepare first hash (pipelined)
    *nonce_ptr = nonce;
    hash_first_fn(vm, blob, blob_size);
    nonce += step;

    int shares_found = 0;

    for (uint32_t i = 1; i < batch_size; i++) {
        uint32_t prev_nonce = nonce - step;

        // Set next input for pipeline
        *nonce_ptr = nonce;

        // Calculate next hash + fetch previous result
        hash_next_fn(vm, blob, blob_size, hash_result);

        // Check if previous hash met the target
        uint64_t hash_val;
        memcpy(&hash_val, hash_result + 24, 8); // Safe unaligned read

        // If share found and we haven't already recorded one in this batch
        if (hash_val < target && shares_found == 0) {
            *out_nonce = prev_nonce;
            memcpy(out_hash, hash_result, 32);
            shares_found = 1;
            // DO NOT return early — we must finish the pipeline to keep state clean
        }

        nonce += step;
    }

    // Process last result
    uint32_t prev_nonce = nonce - step;
    if (hash_last_fn != NULL) {
        hash_last_fn(vm, hash_result);
    } else {
        *nonce_ptr = nonce; // Dummy next input (ignored)
        hash_next_fn(vm, blob, blob_size, hash_result);
    }

    uint64_t hash_val;
    memcpy(&hash_val, hash_result + 24, 8);

    if (hash_val < target && shares_found == 0) {
        *out_nonce = prev_nonce;
        memcpy(out_hash, hash_result, 32);
        shares_found = 1;
    }

    return shares_found; // Return 1 if a share was found, 0 otherwise
}
